"""Characterization of the fair-value formulas as implemented.

No model change is asserted here: the tests lock the current expressions so
docs and callers stay honest about what the functions return.
"""

from __future__ import annotations

import math

import pytest


def jump_compensator(mu_j: float, delta_j: float) -> float:
    return math.exp(mu_j + 0.5 * delta_j * delta_j) - 1.0


def implemented_fair_value(s0: float, q_annual: float, t_years: float, r: float, lam: float, mu_j: float, delta_j: float) -> float:
    """S0 * exp((r - q - λκ) T), κ = exp(μ_J + δ_J²/2) - 1."""
    kappa = jump_compensator(mu_j, delta_j)
    return s0 * math.exp((r - q_annual - lam * kappa) * t_years)


@pytest.mark.pricing
def test_fair_value_matches_compensated_drift_formula():
    import merton_online_calibrator as moc

    p = moc.MertonParams()
    p.sigma = 0.44
    setattr(p, "lambda", 20.0)
    p.mu_j = 0.003
    p.delta_j = 0.01
    cal = moc.OnlineMertonCalibrator(p, moc.CalibratorConfig())

    s0, q_annual, t_years, r = 68_000.0, 0.10, 8.0 / (365.25 * 24.0), 0.0
    got = cal.fair_value(s0, q_annual, t_years, r)
    expected = implemented_fair_value(s0, q_annual, t_years, r, 20.0, 0.003, 0.01)
    assert math.isclose(got, expected, rel_tol=0.0, abs_tol=0.0)

    # Under the compensated SDE in the source comments, the unconditional mean is
    # S0*exp((r-q)T). The implemented formula keeps the -λκ term, so it differs
    # unless λκ = 0. Lock that relationship without changing the code.
    unconditional = s0 * math.exp((r - q_annual) * t_years)
    assert not math.isclose(got, unconditional, rel_tol=0.0, abs_tol=0.0)
    assert math.isclose(got / unconditional, math.exp(-20.0 * jump_compensator(0.003, 0.01) * t_years), rel_tol=0.0, abs_tol=1e-15)


@pytest.mark.pricing
@pytest.mark.parametrize(
    "sigma,lam,mu_j,delta_j,s0,q_annual,t_years,r",
    [
        (0.44, 20.0, 0.003, 0.01, 68_000.0, 0.10, 8.0 / (365.25 * 24.0), 0.0),
        (0.05, 40.0, -0.02, 0.03, 4_400.0, 0.0, 4.0 / (365.25 * 24.0), 0.0),
        (1.2, 0.01, 0.5, 1.0, 100.0, -0.05, 1.0 / 365.25, 0.02),
    ],
)
def test_fair_value_formula_holds_across_params(sigma, lam, mu_j, delta_j, s0, q_annual, t_years, r):
    import merton_online_calibrator as moc

    p = moc.MertonParams()
    p.sigma = sigma
    setattr(p, "lambda", lam)
    p.mu_j = mu_j
    p.delta_j = delta_j
    cal = moc.OnlineMertonCalibrator(p, moc.CalibratorConfig())
    # Construction clamps; read back the params actually stored.
    p = cal.params()
    expected = implemented_fair_value(
        s0, q_annual, t_years, r, float(getattr(p, "lambda")), p.mu_j, p.delta_j
    )
    assert math.isclose(cal.fair_value(s0, q_annual, t_years, r), expected, rel_tol=0.0, abs_tol=0.0)


@pytest.mark.pricing
def test_fair_value_methods_are_finite_and_positive(calibrator):
    price, _ = calibrator.feed_ticks()

    t_years = 8.0 / (365.25 * 24.0)
    fv = calibrator.fair_value(price, 0.10, t_years, 0.0)
    fv_ql = calibrator.fair_value_quantlib(price, 0.10, t_years, 0.0)

    assert math.isfinite(fv)
    assert math.isfinite(fv_ql)
    assert fv > 0.0
    assert fv_ql > 0.0
