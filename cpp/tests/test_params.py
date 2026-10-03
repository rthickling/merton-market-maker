import math

import pytest


@pytest.mark.params
def test_online_update_and_params_are_finite(calibrator):
    price, _ = calibrator.feed_ticks()

    assert calibrator.sample_count() > 0
    params = calibrator.params()
    assert math.isfinite(params.sigma)
    assert math.isfinite(getattr(params, "lambda"))
    assert math.isfinite(params.mu_j)
    assert math.isfinite(params.delta_j)
    assert price > 0


class _Ticks:
    """Feeds accepted returns one at a time without calling maybe_update_params."""

    def __init__(self, cal) -> None:
        self.cal = cal
        self.price = 68_000.0
        self.ts = 1_700_000_000_000_000
        self.i = 0
        cal.update_tick(self.price, self.ts)  # first tick only sets the reference price

    def feed(self, n: int) -> None:
        for _ in range(n):
            self.i += 1
            self.price *= 1.0 + 0.0002 * (1 if self.i % 2 else -1)
            self.ts += 5_000_000
            assert self.cal.update_tick(self.price, self.ts)


def _values(p):
    return (p.sigma, p.lambda_, p.mu_j, p.delta_j)


@pytest.mark.params
def test_gated_calls_do_not_count_as_calibrations(make_calibrator):
    cal = make_calibrator()  # min_points_for_update=64, update_every_n_returns=32
    ticks = _Ticks(cal)
    for _ in range(63):
        ticks.feed(1)
        assert cal.maybe_update_params() is False
    assert cal.sample_count() == 63
    assert cal.calibration_count() == 0


@pytest.mark.params
def test_open_gate_counts_exactly_one_calibration(make_calibrator):
    cal = make_calibrator()
    ticks = _Ticks(cal)
    ticks.feed(64)
    cal.maybe_update_params()
    assert cal.calibration_count() == 1

    # The gate stays closed until update_every_n_returns more returns arrive.
    assert cal.maybe_update_params() is False
    ticks.feed(31)
    assert cal.maybe_update_params() is False
    assert cal.calibration_count() == 1
    ticks.feed(1)
    cal.maybe_update_params()
    assert cal.calibration_count() == 2


@pytest.mark.params
def test_calibration_that_changes_nothing_still_counts(make_calibrator):
    # No candidate can beat an infinite improvement tolerance, so the search
    # runs but keeps the starting parameters.
    cal = make_calibrator(improvement_tol=float("inf"))
    before = _values(cal.params())
    ticks = _Ticks(cal)
    ticks.feed(64)
    assert cal.maybe_update_params() is False
    assert cal.calibration_count() == 1
    assert _values(cal.params()) == before
