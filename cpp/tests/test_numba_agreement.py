"""The Numba variant and the pure-Python reference agree on the same input.

scripts/merton_numba.py is the reference class with its likelihood compiled by
Numba. Counters and accept/reject outcomes must match exactly. Parameters and
likelihoods use the tolerances of test_reference_agreement.py, for the same
reason: compiled code may evaluate exp and log differently in the last bits.
The likelihood is also checked directly, because the calibrator's clamps keep
it from ever passing the parameters for which it returns infinity.
"""

import math
import sys
from pathlib import Path

import pytest

pytest.importorskip("numba")

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts import merton_numba as nb
from scripts import merton_reference as ref
from scripts.synthetic_ticks import PathSpec, merton_ticks

PARAM_REL_TOL = 1e-12
PARAM_ABS_TOL = 1e-15
NLL_REL_TOL = 1e-12

FIELDS = ("sigma", "lambda_", "mu_j", "delta_j")
SMALL_CONFIG = {
    "window_size": 256,
    "min_points_for_update": 128,
    "update_every_n_returns": 64,
    "n_max": 8,
    "coordinate_steps": 2,
}


def _pair(**config):
    cfg = ref.CalibratorConfig(**config)
    return nb.OnlineMertonCalibrator(ref.MertonParams(), cfg), ref.OnlineMertonCalibrator(ref.MertonParams(), cfg)


def _assert_same_state(compiled, py, where: str) -> None:
    assert compiled.calibration_count() == py.calibration_count(), where
    assert compiled.sample_count() == py.sample_count(), where
    compiled_params, py_params = compiled.params(), py.params()
    for name in FIELDS:
        a, b = getattr(compiled_params, name), getattr(py_params, name)
        assert math.isclose(a, b, rel_tol=PARAM_REL_TOL, abs_tol=PARAM_ABS_TOL), (
            f"{where}: {name} Numba {a!r} vs Python {b!r}"
        )


@pytest.mark.numba
def test_likelihood_matches_reference():
    compiled, py = _pair()
    for price, ts in merton_ticks(PathSpec(seed=3, n_ticks=4097)):
        compiled.update_tick(price, ts)
        py.update_tick(price, ts)
    dt = py._estimate_dt_years()
    candidates = (
        ref.MertonParams(),
        ref.MertonParams(sigma=0.6, lambda_=30.0, mu_j=-0.02, delta_j=0.03),
        ref.MertonParams(sigma=0.0),
        ref.MertonParams(lambda_=-1.0),
        ref.MertonParams(delta_j=0.0),
    )
    for p in candidates:
        expected = py._neg_log_likelihood(p, dt)
        got = compiled._neg_log_likelihood(p, dt)
        if math.isinf(expected):
            assert got == expected, p
        else:
            assert math.isclose(got, expected, rel_tol=NLL_REL_TOL, abs_tol=0.0), f"{p}: Numba {got!r} vs Python {expected!r}"


@pytest.mark.numba
def test_agreement_tick_by_tick_through_repeated_calibrations():
    compiled, py = _pair(**SMALL_CONFIG)
    changed = 0
    for i, (price, ts) in enumerate(merton_ticks(PathSpec(seed=11, n_ticks=900))):
        where = f"tick {i}"
        assert compiled.update_tick(price, ts) == py.update_tick(price, ts), where
        compiled_changed = compiled.maybe_update_params()
        assert compiled_changed == py.maybe_update_params(), where
        changed += compiled_changed
        _assert_same_state(compiled, py, where)

    assert compiled.calibration_count() == 13
    assert changed > 0


@pytest.mark.numba
def test_agreement_at_default_configuration():
    window, every = 4096, 128
    compiled, py = _pair()
    ticks = merton_ticks(PathSpec(seed=7, n_ticks=window + 2 * every + 1))
    batches = (ticks[: window + every + 1], ticks[window + every + 1 :])

    for batch in batches:
        for price, ts in batch:
            assert compiled.update_tick(price, ts) == py.update_tick(price, ts)
        assert compiled.maybe_update_params() == py.maybe_update_params()
        _assert_same_state(compiled, py, f"calibration {py.calibration_count()}")

    assert compiled.calibration_count() == 2
    assert compiled.sample_count() == window
