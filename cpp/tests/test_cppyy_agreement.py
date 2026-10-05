"""cppyy and the nanobind module call the same compiled core and agree exactly.

scripts/merton_cppyy.py has cppyy read the header and call
libmerton_core_shared.so, which CMake compiles from the same source, with the
same flags, as the static core inside the nanobind modules. So results must be
identical, not merely close. The library must also export only the public
out-of-line methods: GCC will not inline an exported function into its
callers, so exporting the private helpers would make the shared build's hot
path differ from the modules'. Build it with `just test-manual-bindings`
(GCC only; CMake option MERTON_BUILD_SHARED_CORE=ON).

The bindings themselves differ: cppyy's params() returns a reference to the
calibrator's own parameters where nanobind returns a copy, and cppyy honours
the header's default argument for r.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

try:
    from scripts import merton_cppyy as via_cppyy
except ModuleNotFoundError as exc:
    if exc.name not in ("cppyy", "libmerton_core_shared.so"):
        raise
    pytest.skip("needs cppyy and -DMERTON_BUILD_SHARED_CORE=ON (GCC image)", allow_module_level=True)

import merton_online_calibrator as reflected

from scripts.synthetic_ticks import PathSpec, merton_ticks

pytestmark = pytest.mark.cppyy

PARAM_FIELDS = ("sigma", "lambda_", "mu_j", "delta_j")
HOUR = 1.0 / (365.25 * 24.0)
SMALL_CONFIG = {
    "window_size": 256,
    "min_points_for_update": 128,
    "update_every_n_returns": 64,
    "n_max": 8,
    "coordinate_steps": 2,
}
EXPORTED = {
    "merton_calibrator_size",
    "merton::OnlineMertonCalibrator::OnlineMertonCalibrator(merton::MertonParams, merton::CalibratorConfig)",
    "merton::OnlineMertonCalibrator::update_tick(double, long)",
    "merton::OnlineMertonCalibrator::maybe_update_params()",
    "merton::OnlineMertonCalibrator::fair_value(double, double, double, double) const",
    "merton::OnlineMertonCalibrator::fair_value_quantlib(double, double, double, double) const",
}


def _make(module, **config):
    cfg = module.CalibratorConfig()
    for name, value in config.items():
        setattr(cfg, name, value)
    return module.OnlineMertonCalibrator(module.MertonParams(), cfg)


def _state(cal) -> tuple:
    p = cal.params()
    return (cal.sample_count(), cal.calibration_count(), *(getattr(p, name) for name in PARAM_FIELDS))


def test_library_exports_only_the_public_methods():
    nm = shutil.which("nm")
    if nm is None:
        pytest.skip("needs nm (binutils)")
    listing = subprocess.run(
        [nm, "-DC", "--defined-only", str(via_cppyy._library)], check=True, capture_output=True, text=True
    ).stdout
    symbols = {line.split(" ", 2)[2] for line in listing.splitlines()}
    assert {s for s in symbols if s.startswith("merton")} == EXPORTED
    assert not [s for s in symbols if "QuantLib" in s and not s.startswith("merton")]


def test_same_results_tick_by_tick():
    a, b = _make(reflected, **SMALL_CONFIG), _make(via_cppyy, **SMALL_CONFIG)
    for i, (price, ts) in enumerate(merton_ticks(PathSpec(seed=3, n_ticks=600))):
        where = f"tick {i}"
        assert a.update_tick(price, ts) == b.update_tick(price, ts), where
        assert a.maybe_update_params() == b.maybe_update_params(), where
        assert _state(a) == _state(b), where
        assert a.fair_value(price, 0.1, HOUR, 0.0) == b.fair_value(price, 0.1, HOUR, 0.0), where

    assert b.calibration_count() == 8
    assert a.fair_value_quantlib(100.0, 0.1, 0.5, 0.02) == b.fair_value_quantlib(100.0, 0.1, 0.5, 0.02)


def test_same_results_at_default_configuration():
    window, every = 4096, 128
    a, b = _make(reflected), _make(via_cppyy)
    ticks = merton_ticks(PathSpec(seed=7, n_ticks=window + 2 * every + 1))

    for batch in (ticks[: window + every + 1], ticks[window + every + 1 :]):
        for price, ts in batch:
            assert a.update_tick(price, ts) == b.update_tick(price, ts)
        assert a.maybe_update_params() == b.maybe_update_params()
        assert _state(a) == _state(b)

    assert b.calibration_count() == 2
    assert b.sample_count() == window


def test_binding_semantics():
    p = via_cppyy.MertonParams()
    p.lambda_ = 12.5
    assert getattr(p, "lambda") == 12.5

    cal = via_cppyy.OnlineMertonCalibrator(p)
    view = cal.params()
    view.sigma = 2.0
    assert cal.params().sigma == 2.0
    assert cal.fair_value(100.0, 0.1, 0.5) == cal.fair_value(100.0, 0.1, 0.5, 0.0)
    with pytest.raises(TypeError):
        cal.update_tick("100", 1)
