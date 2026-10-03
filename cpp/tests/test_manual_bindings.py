"""The hand-written nanobind module stays equivalent to the reflected one.

merton_manual_bindings is the baseline that benchmarks compare the reflected
module against. It is written by hand but makes the same binding choices, so a
difference in measured cost cannot come from the two modules doing different
work. These tests fail when the two drift apart. Build it with
`just test-manual-bindings` (CMake option MERTON_BUILD_MANUAL_BINDINGS=ON).
"""

import sys
from pathlib import Path

import pytest

try:
    import merton_manual_bindings as manual
except ModuleNotFoundError as exc:
    if exc.name != "merton_manual_bindings":
        raise
    pytest.skip("built only with -DMERTON_BUILD_MANUAL_BINDINGS=ON", allow_module_level=True)

import merton_online_calibrator as reflected

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.synthetic_ticks import PathSpec, merton_ticks

pytestmark = pytest.mark.equivalence

BOTH_MODULES = pytest.mark.parametrize("module", [reflected, manual], ids=["reflected", "manual"])
CLASSES = ("MertonParams", "CalibratorConfig", "OnlineMertonCalibrator")
PARAM_FIELDS = ("sigma", "lambda_", "mu_j", "delta_j")
HOUR = 1.0 / (365.25 * 24.0)


def _public_names(obj) -> list[str]:
    return sorted(n for n in dir(obj) if not n.startswith("_"))


def _surface(module) -> dict[str, tuple[str, ...]]:
    """Every public name with its signatures, minus the module name."""
    prefix = f"{module.__name__}."
    surface = {"<module>": tuple(_public_names(module))}
    for cls_name in CLASSES:
        cls = getattr(module, cls_name)
        for name in [*_public_names(cls), "__init__", "__repr__"]:
            member = getattr(cls, name)
            if isinstance(member, property):
                docs = (member.fget.__doc__, member.fset.__doc__)
            else:
                docs = (member.__doc__,)
            surface[f"{cls_name}.{name}"] = tuple(doc.replace(prefix, "") for doc in docs)
    return surface


def test_same_names_and_signatures():
    surface = _surface(manual)
    assert surface == _surface(reflected)
    assert "OnlineMertonCalibrator.calibration_count" in surface
    assert "MertonParams.lambda" in surface


def test_modules_keep_separate_types():
    assert manual.MertonParams is not reflected.MertonParams
    with pytest.raises(TypeError):
        manual.OnlineMertonCalibrator(reflected.MertonParams())


def test_same_repr():
    for cls_name in ("MertonParams", "CalibratorConfig"):
        assert repr(getattr(manual, cls_name)()) == repr(getattr(reflected, cls_name)())


@BOTH_MODULES
def test_argument_handling(module):
    cal = module.OnlineMertonCalibrator(module.MertonParams(), config=module.CalibratorConfig())
    assert cal.fair_value(s0=100.0, q_annual=0.1, t_years=0.5, r=0.02) == cal.fair_value(100.0, 0.1, 0.5, 0.02)
    with pytest.raises(TypeError):
        cal.fair_value(100.0, 0.1, 0.5)
    with pytest.raises(TypeError):
        cal.update_tick("100", 1)


@BOTH_MODULES
def test_alias_and_snapshot_semantics(module):
    p = module.MertonParams()
    p.lambda_ = 12.5
    assert getattr(p, "lambda") == 12.5
    setattr(p, "lambda", 7.0)
    assert p.lambda_ == 7.0

    cal = module.OnlineMertonCalibrator(p)
    snapshot = cal.params()
    snapshot.sigma = 2.0
    assert cal.params().sigma != 2.0


def test_same_results_on_the_same_ticks():
    def make(module):
        cfg = module.CalibratorConfig()
        cfg.window_size = 256
        cfg.min_points_for_update = 128
        cfg.update_every_n_returns = 64
        cfg.n_max = 8
        cfg.coordinate_steps = 2
        return module.OnlineMertonCalibrator(module.MertonParams(), cfg)

    a, b = make(reflected), make(manual)
    for price, ts in merton_ticks(PathSpec(seed=3, n_ticks=600)):
        assert a.update_tick(price, ts) == b.update_tick(price, ts)
        assert a.maybe_update_params() == b.maybe_update_params()
        assert [getattr(a.params(), f) for f in PARAM_FIELDS] == [getattr(b.params(), f) for f in PARAM_FIELDS]
        assert a.fair_value(price, 0.1, HOUR, 0.0) == b.fair_value(price, 0.1, HOUR, 0.0)
    assert a.calibration_count() == b.calibration_count() > 0
