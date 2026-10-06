"""The model-driven paper-quote width in scripts/merton_runtime.py.

Fixed parameter snapshots and a small fake calibrator keep these deterministic;
one test runs the compiled calibrator when it is importable.
"""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts import merton_runtime as rt
from scripts.merton_runtime import (
    LogThrottle,
    ParamSnapshot,
    QuoteError,
    QuotePolicy,
    log_return_variance,
    model_quote,
    quote_payload,
    quote_tick,
)

SEEDS = ParamSnapshot(sigma=0.44, lam=20.0, mu_j=0.003, delta_j=0.01)
JUMPIER = ParamSnapshot(sigma=0.44, lam=40.0, mu_j=-0.05, delta_j=0.05)
CALM = ParamSnapshot(sigma=0.05, lam=0.01, mu_j=0.0, delta_j=0.01)
POLICY = QuotePolicy()
TIGHT_BOOK = (59_999.95, 60_000.05)  # mid 60,000, under 0.01 bp of market half-spread


def quote(params, book=TIGHT_BOOK, policy=POLICY, calibrated=True):
    return model_quote(*book, params, policy, calibrated=calibrated)


class FakeCalibrator:
    """Holds `before` until the tick numbered `refit_on`, whose calibration moves it to `after`."""

    def __init__(self, before, after, refit_on):
        self._params, self._after, self._refit_on = before, after, refit_on
        self.ticks = 0
        self.calibrations = 0

    def update_tick(self, price, epoch_us):
        self.ticks += 1
        return True

    def maybe_update_params(self):
        if self.ticks != self._refit_on:
            return False
        self.calibrations += 1
        changed = self._after != self._params
        self._params = self._after
        return changed

    def params(self):
        p = self._params
        return SimpleNamespace(sigma=p.sigma, mu_j=p.mu_j, delta_j=p.delta_j, **{"lambda": p.lam})

    def calibration_count(self):
        return self.calibrations


def test_variance_includes_the_squared_mean_jump_as_well_as_the_jump_spread():
    jumps_only = ParamSnapshot(sigma=0.0, lam=10.0, mu_j=0.03, delta_j=0.04)
    assert log_return_variance(jumps_only, 1.0) == pytest.approx(10.0 * (0.03**2 + 0.04**2), rel=1e-15)
    assert log_return_variance(jumps_only, 1.0) > 10.0 * 0.04**2
    assert log_return_variance(SEEDS, 0.5) == pytest.approx(0.5 * (0.44**2 + 20.0 * (0.003**2 + 0.01**2)), rel=1e-15)


@pytest.mark.parametrize("factor", [0.25, 4.0, 60.0])
def test_variance_scales_with_the_horizon_and_the_width_with_its_square_root(factor):
    t = POLICY.risk_horizon_years
    assert log_return_variance(SEEDS, factor * t) == pytest.approx(factor * log_return_variance(SEEDS, t), rel=1e-12)
    base = quote(SEEDS)
    longer = quote(SEEDS, policy=QuotePolicy(risk_horizon_seconds=factor * POLICY.risk_horizon_seconds))
    assert longer.model_half_spread == pytest.approx(math.sqrt(factor) * base.model_half_spread, rel=1e-12)
    assert base.set_by == longer.set_by == "model"
    assert longer.half_spread == pytest.approx(math.sqrt(factor) * base.half_spread, rel=1e-12)


@pytest.mark.parametrize(
    "field,values",
    [
        ("sigma", [0.0, 0.05, 0.44, 1.0, 3.0]),
        ("lam", [0.0, 0.01, 20.0, 40.0, 500.0]),
        ("delta_j", [0.0, 0.01, 0.05, 1.0]),
        ("mu_j", [0.0, 0.003, 0.05, 0.5]),
        ("mu_j", [0.0, -0.003, -0.05, -0.5]),
    ],
)
def test_the_width_never_narrows_as_a_parameter_grows(field, values):
    quotes = [quote(SEEDS._replace(**{field: v})) for v in values]
    model = [q.model_half_spread for q in quotes]
    final = [q.half_spread for q in quotes]
    assert model == sorted(model)
    assert final == sorted(final)


def test_the_sign_of_the_mean_jump_does_not_change_the_quote():
    up, down = quote(SEEDS._replace(mu_j=0.02)), quote(SEEDS._replace(mu_j=-0.02))
    assert (up.bid, up.ask, up.half_spread, up.set_by) == (down.bid, down.ask, down.half_spread, down.set_by)


@pytest.mark.parametrize("book", [TIGHT_BOOK, (59_990.0, 60_010.0), (100.0, 100.0), (0.5123, 0.5125)])
@pytest.mark.parametrize("params", [SEEDS, JUMPIER, CALM])
def test_quotes_are_centred_on_the_market_midpoint(book, params):
    q = quote(params, book=book)
    assert q.mid == (book[0] + book[1]) / 2.0
    assert (q.bid + q.ask) / 2.0 == pytest.approx(q.mid, rel=1e-15)
    assert q.ask - q.mid == pytest.approx(q.half_spread, abs=1e-12 * q.mid)
    assert q.mid - q.bid == pytest.approx(q.half_spread, abs=1e-12 * q.mid)
    assert q.bid <= book[0] and q.ask >= book[1]
    assert q.half_spread == max(q.market_half_spread, q.floor_half_spread, q.model_half_spread)


def test_a_wide_market_spread_sets_the_width():
    q = quote(SEEDS, book=(59_900.0, 60_100.0))
    assert q.set_by == "market spread"
    assert (q.bid, q.ask) == (59_900.0, 60_100.0)
    assert q.model_half_spread < q.half_spread


def test_the_floor_sets_the_width_when_the_model_is_calm():
    q = quote(CALM)
    assert q.set_by == "floor"
    assert q.half_spread == pytest.approx(60_000.0 * 2.0 / 10_000, rel=1e-12)
    assert (round(q.bid, 2), round(q.ask, 2)) == (59_988.00, 60_012.00)
    assert q.bps(q.model_half_spread) == pytest.approx(0.69, abs=0.005)


def test_a_tie_goes_to_the_market_spread_then_the_floor():
    assert quote(CALM, book=(96.0, 104.0), policy=QuotePolicy(min_half_spread_bps=400.0)).set_by == "market spread"


def test_with_the_model_setting_the_width_new_parameters_change_the_quote():
    seeded, jumpier = quote(SEEDS, calibrated=False), quote(JUMPIER)
    assert seeded.mid == jumpier.mid == 60_000.0
    assert seeded.set_by == jumpier.set_by == "model"
    assert (round(seeded.bid, 2), round(seeded.ask, 2)) == (59_963.39, 60_036.61)
    assert (round(jumpier.bid, 2), round(jumpier.ask, 2)) == (59_948.10, 60_051.90)
    assert seeded.describe() == (
        "half=6.10bp set by model (model 6.10bp, floor 2.00bp, market 0.01bp; 60s horizon; params seeded)"
    )
    assert jumpier.describe() == (
        "half=8.65bp set by model (model 8.65bp, floor 2.00bp, market 0.01bp; 60s horizon; params calibrated)"
    )


def test_a_refit_sets_the_width_of_the_tick_that_produced_it():
    cal = FakeCalibrator(SEEDS, JUMPIER, refit_on=2)
    (t1, q1), (t2, q2), (t3, q3) = (quote_tick(cal, *TIGHT_BOOK, i * 1_000_000, POLICY) for i in (1, 2, 3))
    assert not t1.params_updated and q1.params == SEEDS and q1.params_source == "seeded"
    assert t2.params_updated and q2.params == JUMPIER and q2.params_source == "calibrated"
    assert q2.half_spread > q1.half_spread
    assert not t3.params_updated and q3 == q2


def test_a_calibration_that_keeps_the_seeds_still_counts_as_calibrated():
    cal = FakeCalibrator(SEEDS, SEEDS, refit_on=2)
    (t1, q1), (t2, q2) = (quote_tick(cal, *TIGHT_BOOK, i, POLICY) for i in (1, 2))
    assert q1.params_source == "seeded" and "params seeded" in q1.describe()
    assert not t2.params_updated
    assert q2.params_source == "calibrated" and q2.params == SEEDS


def test_the_compiled_calibrator_sets_the_width_on_the_refit_tick():
    moc = pytest.importorskip("merton_online_calibrator")
    from scripts.synthetic_ticks import PathSpec, merton_ticks

    cal = rt.build_calibrator(moc, seeds={"sigma": 0.44, "lambda": 20.0, "mu_j": 0.003, "delta_j": 0.01})
    previous, refits = None, 0
    for price, epoch_us in merton_ticks(PathSpec(seed=7, n_ticks=1_200)):
        tick, q = quote_tick(cal, price, price, epoch_us, POLICY)
        p = cal.params()
        assert q.params == ParamSnapshot(p.sigma, getattr(p, "lambda"), p.mu_j, p.delta_j)
        assert q.params_source == ("calibrated" if cal.calibration_count() else "seeded")
        if tick.params_updated:
            refits += 1
            assert q.params != previous.params and q.model_half_spread != previous.model_half_spread
        previous = q
    assert refits > 0


@pytest.mark.parametrize(
    "book,message",
    [
        ((math.nan, 100.0), "market bid nan"),
        ((100.0, math.inf), "market ask inf"),
        ((0.0, 100.0), "market bid 0.0 "),
        ((-1.0, 100.0), "market bid -1.0 "),
        ((100.1, 100.0), "crossed book"),
    ],
)
def test_a_bad_book_is_rejected_before_the_calibrator_sees_it(book, message):
    cal = FakeCalibrator(SEEDS, JUMPIER, refit_on=1)
    with pytest.raises(QuoteError, match=message):
        quote_tick(cal, *book, 1, POLICY)
    assert cal.ticks == 0


@pytest.mark.parametrize(
    "params,message",
    [
        (SEEDS._replace(sigma=math.nan), "sigma=nan is not finite"),
        (SEEDS._replace(lam=math.inf), "lam=inf is not finite"),
        (SEEDS._replace(mu_j=-math.inf), "mu_j=-inf is not finite"),
        (SEEDS._replace(sigma=-0.1), "sigma=-0.1 is negative"),
        (SEEDS._replace(lam=-1.0), "lam=-1.0 is negative"),
        (SEEDS._replace(delta_j=-0.01), "delta_j=-0.01 is negative"),
        (SEEDS._replace(sigma=1e200), "variance inf is not finite"),
    ],
)
def test_unusable_parameters_are_rejected_not_replaced_by_the_floor(params, message):
    with pytest.raises(QuoteError, match=message):
        quote(params)


def test_unusable_quotes_are_rejected():
    with pytest.raises(QuoteError, match="bid .* is not positive"):
        quote(SEEDS, policy=QuotePolicy(risk_multiplier=2_000.0))
    with pytest.raises(QuoteError, match="zero-width"):
        quote(ParamSnapshot(0.0, 0.0, 0.0, 0.0), book=(100.0, 100.0), policy=QuotePolicy(min_half_spread_bps=0.0))
    with pytest.raises(QuoteError, match="not finite"):
        quote(SEEDS, book=(1e308, 1.7e308))


@pytest.mark.parametrize(
    "settings",
    [
        {"risk_horizon_seconds": 0.0},
        {"risk_horizon_seconds": -60.0},
        {"risk_horizon_seconds": math.nan},
        {"risk_multiplier": 0.0},
        {"risk_multiplier": math.inf},
        {"min_half_spread_bps": -1.0},
        {"min_half_spread_bps": math.nan},
    ],
)
def test_invalid_policy_settings_are_rejected(settings):
    with pytest.raises(ValueError, match=next(iter(settings))):
        QuotePolicy(**settings)


def test_policy_from_env(monkeypatch):
    for var in ("MERTON_RISK_HORIZON_SECONDS", "MERTON_RISK_MULTIPLIER", "MERTON_MIN_HALF_SPREAD_BPS"):
        monkeypatch.delenv(var, raising=False)
    assert QuotePolicy.from_env() == QuotePolicy() == QuotePolicy(60.0, 1.0, 2.0)
    monkeypatch.setenv("MERTON_RISK_HORIZON_SECONDS", "300")
    monkeypatch.setenv("MERTON_RISK_MULTIPLIER", "2")
    monkeypatch.setenv("MERTON_MIN_HALF_SPREAD_BPS", "")
    assert QuotePolicy.from_env() == QuotePolicy(risk_horizon_seconds=300.0, risk_multiplier=2.0)
    for bad in ("two", "-1", "nan"):
        monkeypatch.setenv("MERTON_RISK_MULTIPLIER", bad)
        with pytest.raises(ValueError, match="MERTON_RISK_MULTIPLIER"):
            QuotePolicy.from_env()


def test_log_throttle():
    once_a_second = LogThrottle(1.0)
    assert [once_a_second.ready(now) for now in (0.0, 0.5, 1.0, 1.2, 2.5)] == [True, False, True, False, True]
    assert all(LogThrottle(0.0).ready(now) for now in (0.0, 0.0, 0.1))
    with pytest.raises(ValueError):
        LogThrottle(-1.0)


def test_the_payload_keeps_its_earlier_fields_and_adds_the_width():
    q = quote(SEEDS)
    payload = quote_payload("BTCUSDT", q, no_jump_mean=59_999.0, vs_mid_bps=-0.2)
    assert payload["sym"] == "BTCUSDT"
    assert payload["market"] == q.mid and payload["quote_reference"] == "mid"
    assert (payload["quote_bid"], payload["quote_ask"]) == (q.bid, q.ask)
    assert payload["theo"] == payload["no_jump_conditional_mean"] == 59_999.0
    assert payload["diff_bps"] == payload["no_jump_mean_vs_mid_bps"] == -0.2
    assert payload["half_spread_bps"] == payload["model_half_spread_bps"] == pytest.approx(6.10, abs=0.005)
    assert payload["floor_half_spread_bps"] == pytest.approx(2.0, rel=1e-12)
    assert payload["market_half_spread_bps"] < 0.01
    assert (payload["width_set_by"], payload["params_source"], payload["risk_horizon_seconds"]) == (
        "model",
        "calibrated",
        60.0,
    )


def test_existing_helpers_aliases_and_keyword_callers_still_work():
    assert rt.merton_theoretical is rt.no_jump_conditional_mean
    assert rt.paper_quotes(theo=100.0, mkt_bid=99.99, mkt_ask=100.01, min_half_spread_bps=2.0) == rt.paper_quotes(
        100.0, 99.99, 100.01, 2.0
    )
    assert rt.midpoint_quotes(99.0, 101.0, min_half_spread_bps=2.0) == (100.0, 99.0, 101.0)
    tick = rt.tick_calibrator(FakeCalibrator(SEEDS, JUMPIER, refit_on=1), 100.0, 1)
    assert tick.params_updated and (tick.sigma, tick.lam, tick.mu_j, tick.delta_j) == tuple(JUMPIER)
    by_keyword = model_quote(mkt_bid=TIGHT_BOOK[0], mkt_ask=TIGHT_BOOK[1], params=SEEDS, policy=POLICY, calibrated=True)
    assert by_keyword == quote(SEEDS)
    with pytest.raises(TypeError):
        model_quote(*TIGHT_BOOK, SEEDS, POLICY, True)
    _, q = quote_tick(
        calibrator=FakeCalibrator(SEEDS, JUMPIER, refit_on=9),
        mkt_bid=TIGHT_BOOK[0],
        mkt_ask=TIGHT_BOOK[1],
        epoch_us=1,
        policy=POLICY,
    )
    assert q == quote(SEEDS, calibrated=False)
