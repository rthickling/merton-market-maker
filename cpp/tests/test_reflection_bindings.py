import math

import pytest


@pytest.mark.reflection
def test_merton_params_property_roundtrip(calibrator):
    p = calibrator.params()
    p.sigma = 0.55
    p.mu_j = -0.01
    p.delta_j = 0.02
    setattr(p, "lambda", 12.5)

    assert p.sigma == pytest.approx(0.55)
    assert p.mu_j == pytest.approx(-0.01)
    assert p.delta_j == pytest.approx(0.02)
    assert getattr(p, "lambda") == pytest.approx(12.5)

    text = repr(p)
    assert "sigma" in text
    assert "lambda" in text


@pytest.mark.reflection
def test_calibrator_config_properties_and_methods(calibrator):
    # Method calls exposed via reflection
    assert calibrator.sample_count() == 0
    price, ts = calibrator.feed_ticks()
    assert calibrator.sample_count() > 0
    assert price > 0
    assert ts > 0

    updated = calibrator.cal.maybe_update_params()
    assert isinstance(updated, bool)

    params = calibrator.params()
    assert math.isfinite(params.sigma)
    assert math.isfinite(getattr(params, "lambda"))

    t_years = 8.0 / (365.25 * 24.0)
    mean = calibrator.no_jump_conditional_mean(price, 0.10, t_years, 0.0)
    assert math.isfinite(mean) and mean > 0.0
