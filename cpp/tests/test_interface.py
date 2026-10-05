"""The Python interface the reflection bindings produce, on either backend."""

import re

import pytest

T_8H = 8.0 / (365.25 * 24.0)

MEAN_METHODS = (
    "no_jump_conditional_mean",
    "no_jump_conditional_mean_quantlib",
    "fair_value",
    "fair_value_quantlib",
)
PUBLIC_METHODS = {
    "update_tick",
    "maybe_update_params",
    *MEAN_METHODS,
    "params",
    "sample_count",
    "calibration_count",
}
PRIVATE_HELPERS = {"merton_pdf", "neg_log_likelihood", "clamp_params", "estimate_dt_years"}


def _moc():
    import merton_online_calibrator as moc

    return moc


def _values(p):
    return (p.sigma, p.lambda_, p.mu_j, p.delta_j)


@pytest.mark.reflection
def test_only_public_methods_are_exposed():
    names = {n for n in dir(_moc().OnlineMertonCalibrator) if not n.startswith("_")}
    assert PUBLIC_METHODS <= names
    assert not PRIVATE_HELPERS & names


@pytest.mark.reflection
@pytest.mark.parametrize(
    ("method", "names"),
    [
        ("update_tick", ("price", "epoch_us")),
        *((method, ("s0", "q_annual", "t_years", "r")) for method in MEAN_METHODS),
    ],
)
def test_signatures_show_parameter_names(method, names):
    # Type spellings differ between nanobind and pybind11; the names must not.
    doc = getattr(_moc().OnlineMertonCalibrator, method).__doc__
    parameters = ", ".join(rf"{name}: [^,)]+" for name in names)
    assert re.search(rf"{method}\(self[^,)]*, {parameters}\)", doc), doc


@pytest.mark.reflection
def test_keyword_and_positional_calls_agree(calibrator):
    price, ts = calibrator.feed_ticks()
    cal = calibrator.cal
    for method in MEAN_METHODS:
        fn = getattr(cal, method)
        assert fn(price, 0.10, T_8H, 0.0) == fn(s0=price, q_annual=0.10, t_years=T_8H, r=0.0)
    assert cal.update_tick(price=price * 1.0001, epoch_us=ts + 5_000_000) is True


@pytest.mark.reflection
@pytest.mark.parametrize("method", MEAN_METHODS)
def test_cpp_default_arguments_are_not_python_defaults(calibrator, method):
    # Each declares r = 0.0 in C++, but the bindings do not generate
    # Python defaults, so r must be passed.
    with pytest.raises(TypeError):
        getattr(calibrator.cal, method)(68_000.0, 0.10, T_8H)


@pytest.mark.reflection
def test_lambda_alias_shares_storage_with_lambda():
    p = _moc().MertonParams()
    assert p.lambda_ == getattr(p, "lambda") == pytest.approx(20.0)
    p.lambda_ = 7.5
    assert getattr(p, "lambda") == pytest.approx(7.5)
    setattr(p, "lambda", 3.0)
    assert p.lambda_ == pytest.approx(3.0)


@pytest.mark.reflection
def test_params_returns_a_snapshot(calibrator):
    cal = calibrator.cal
    snapshot = cal.params()
    snapshot.sigma = 2.5
    snapshot.lambda_ = 1.0
    current = cal.params()
    assert current is not snapshot
    assert _values(current) == pytest.approx((0.44, 20.0, 0.003, 0.01))


@pytest.mark.reflection
def test_params_snapshot_is_unaffected_by_later_calibration(calibrator):
    snapshot = calibrator.params()
    before = _values(snapshot)
    calibrator.feed_ticks()
    assert _values(calibrator.params()) != before, "precondition: calibration moved the parameters"
    assert _values(snapshot) == before


@pytest.mark.reflection
def test_repr_lists_every_field():
    moc = _moc()
    text = repr(moc.MertonParams())
    for name in ("sigma", "lambda", "mu_j", "delta_j"):
        assert f"{name}=" in text
    text = repr(moc.CalibratorConfig())
    for name in (
        "window_size",
        "min_points_for_update",
        "n_max",
        "update_every_n_returns",
        "coordinate_steps",
        "improvement_tol",
    ):
        assert f"{name}=" in text
