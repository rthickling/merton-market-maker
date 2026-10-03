"""The C++ module and the pure-Python reference agree on the same input.

Agreement shows the two implementations are consistent with each other. It is
not an independent validation of the Merton model or of its calibration.

Counters and accept/reject outcomes must match exactly. When every decision
matches, both sides apply the same IEEE-754 operations in the same order, so
the only remaining source of difference is the C library's exp and log, which
can differ by a few ulps between platforms. Parameters therefore use a
relative tolerance of 1e-12 (about 4,500 ulps), which is still more than eight
orders of magnitude below the smallest step the coordinate search takes
(relative 1e-4 or more), so a differing decision cannot hide inside it. The
absolute term covers mu_j near zero. fair_value is one exp and a few products,
hence 1e-13. A failure is a divergence to investigate, not a tolerance to widen.
"""

import math
import sys
from pathlib import Path

import pytest

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts import merton_reference as ref
from scripts.synthetic_ticks import PathSpec, merton_ticks

PARAM_REL_TOL = 1e-12
PARAM_ABS_TOL = 1e-15
FAIR_VALUE_REL_TOL = 1e-13

FIELDS = ("sigma", "lambda_", "mu_j", "delta_j")
START = {"sigma": 0.44, "lambda_": 20.0, "mu_j": 0.003, "delta_j": 0.01}
SMALL_CONFIG = {
    "window_size": 256,
    "min_points_for_update": 128,
    "update_every_n_returns": 64,
    "n_max": 8,
    "coordinate_steps": 2,
}
HOURS = 1.0 / (365.25 * 24.0)
HORIZONS = (1 * HOURS, 8 * HOURS, 24 * HOURS)


def _pair(**config):
    import merton_online_calibrator as moc

    p = moc.MertonParams()
    for name, value in START.items():
        setattr(p, name, value)
    cfg = moc.CalibratorConfig()
    for name, value in config.items():
        setattr(cfg, name, value)
    cpp = moc.OnlineMertonCalibrator(p, cfg)
    py = ref.OnlineMertonCalibrator(ref.MertonParams(**START), ref.CalibratorConfig(**config))
    return cpp, py


def _assert_same_state(cpp, py, where: str) -> None:
    assert cpp.calibration_count() == py.calibration_count(), where
    assert cpp.sample_count() == py.sample_count(), where
    cpp_params, py_params = cpp.params(), py.params()
    for name in FIELDS:
        a, b = getattr(cpp_params, name), getattr(py_params, name)
        assert math.isclose(a, b, rel_tol=PARAM_REL_TOL, abs_tol=PARAM_ABS_TOL), (
            f"{where}: {name} C++ {a!r} vs Python {b!r}"
        )


def _assert_same_fair_value(cpp, py, price: float, where: str) -> None:
    for t in HORIZONS:
        a = cpp.fair_value(price, 0.1, t, 0.0)
        b = py.fair_value(price, 0.1, t, 0.0)
        assert math.isclose(a, b, rel_tol=FAIR_VALUE_REL_TOL, abs_tol=0.0), (
            f"{where}: fair_value(t={t!r}) C++ {a!r} vs Python {b!r}"
        )


def test_reference_defaults_match_cpp():
    import merton_online_calibrator as moc

    for cpp_obj, py_obj in (
        (moc.MertonParams(), ref.MertonParams()),
        (moc.CalibratorConfig(), ref.CalibratorConfig()),
    ):
        for name, value in vars(py_obj).items():
            assert getattr(cpp_obj, name) == value, name


@pytest.mark.agreement
def test_agreement_tick_by_tick_through_repeated_calibrations():
    cpp, py = _pair(**SMALL_CONFIG)
    changed = 0
    for i, (price, ts) in enumerate(merton_ticks(PathSpec(seed=11, n_ticks=900))):
        where = f"tick {i}"
        assert cpp.update_tick(price, ts) == py.update_tick(price, ts), where
        cpp_changed = cpp.maybe_update_params()
        assert cpp_changed == py.maybe_update_params(), where
        changed += cpp_changed
        _assert_same_state(cpp, py, where)
        _assert_same_fair_value(cpp, py, price, where)

    assert cpp.calibration_count() == 13
    assert changed > 0


@pytest.mark.agreement
def test_agreement_at_default_configuration():
    window, every = 4096, 128
    cpp, py = _pair()
    ticks = merton_ticks(PathSpec(seed=7, n_ticks=window + 2 * every + 1))
    batches = (ticks[: window + every + 1], ticks[window + every + 1 :])

    for batch in batches:
        for price, ts in batch:
            assert cpp.update_tick(price, ts) == py.update_tick(price, ts)
        assert cpp.maybe_update_params() == py.maybe_update_params()
        where = f"calibration {cpp.calibration_count()}"
        _assert_same_state(cpp, py, where)
        _assert_same_fair_value(cpp, py, batch[-1][0], where)

    assert cpp.calibration_count() == 2
    assert cpp.sample_count() == window
