"""Characterization of the no-jump conditional mean as implemented.

No model change is asserted here: the tests lock the current expressions so
docs and callers stay honest about what the functions return, and check that
the former names, fair_value and fair_value_quantlib, return the same numbers.
"""

from __future__ import annotations

import math

import pytest

T_8H = 8.0 / (365.25 * 24.0)


def jump_compensator(mu_j: float, delta_j: float) -> float:
    return math.exp(mu_j + 0.5 * delta_j * delta_j) - 1.0


def implemented_no_jump_mean(s0: float, q_annual: float, t_years: float, r: float, lam: float, mu_j: float, delta_j: float) -> float:
    """S0 * exp((r - q - λκ) T), κ = exp(μ_J + δ_J²/2) - 1."""
    kappa = jump_compensator(mu_j, delta_j)
    return s0 * math.exp((r - q_annual - lam * kappa) * t_years)


def _calibrator(sigma: float, lam: float, mu_j: float, delta_j: float):
    import merton_online_calibrator as moc

    p = moc.MertonParams()
    p.sigma = sigma
    setattr(p, "lambda", lam)
    p.mu_j = mu_j
    p.delta_j = delta_j
    return moc.OnlineMertonCalibrator(p, moc.CalibratorConfig())


@pytest.mark.pricing
def test_no_jump_conditional_mean_matches_compensated_drift_formula():
    cal = _calibrator(0.44, 20.0, 0.003, 0.01)

    s0, q_annual, t_years, r = 68_000.0, 0.10, T_8H, 0.0
    got = cal.no_jump_conditional_mean(s0, q_annual, t_years, r)
    expected = implemented_no_jump_mean(s0, q_annual, t_years, r, 20.0, 0.003, 0.01)
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
        (0.44, 20.0, 0.003, 0.01, 68_000.0, 0.10, T_8H, 0.0),
        (0.05, 40.0, -0.02, 0.03, 4_400.0, 0.0, 4.0 / (365.25 * 24.0), 0.0),
        (1.2, 0.01, 0.5, 1.0, 100.0, -0.05, 1.0 / 365.25, 0.02),
    ],
)
def test_no_jump_conditional_mean_formula_holds_across_params(sigma, lam, mu_j, delta_j, s0, q_annual, t_years, r):
    cal = _calibrator(sigma, lam, mu_j, delta_j)
    # Construction clamps; read back the params actually stored.
    p = cal.params()
    expected = implemented_no_jump_mean(
        s0, q_annual, t_years, r, float(getattr(p, "lambda")), p.mu_j, p.delta_j
    )
    assert math.isclose(cal.no_jump_conditional_mean(s0, q_annual, t_years, r), expected, rel_tol=0.0, abs_tol=0.0)


@pytest.mark.pricing
def test_no_jump_conditional_means_are_finite_and_positive(calibrator):
    price, _ = calibrator.feed_ticks()

    mean = calibrator.no_jump_conditional_mean(price, 0.10, T_8H, 0.0)
    mean_ql = calibrator.no_jump_conditional_mean_quantlib(price, 0.10, T_8H, 0.0)

    assert math.isfinite(mean)
    assert math.isfinite(mean_ql)
    assert mean > 0.0
    assert mean_ql > 0.0


ARGUMENTS = [
    (68_000.0, 0.10, T_8H, 0.0),
    (4_400.0, 0.0, 4.0 / (365.25 * 24.0), 0.0),
    (100.0, -0.05, 1.0 / 365.25, 0.02),
    (1.5, 0.3, 2.0, -0.01),
    (68_000.0, 0.10, 1e-9, 0.05),
]


@pytest.mark.pricing
@pytest.mark.parametrize(
    "sigma,lam,mu_j,delta_j",
    [
        (0.44, 20.0, 0.003, 0.01),
        (10.0, 1e6, 5.0, 10.0),  # clamped to the upper bounds
        (0.0, 0.0, -5.0, 0.0),  # clamped to the lower bounds
    ],
)
def test_former_names_return_identical_results(sigma, lam, mu_j, delta_j):
    cal = _calibrator(sigma, lam, mu_j, delta_j)
    for args in ARGUMENTS:
        assert cal.fair_value(*args) == cal.no_jump_conditional_mean(*args), args
        assert cal.fair_value_quantlib(*args) == cal.no_jump_conditional_mean_quantlib(*args), args


@pytest.mark.pricing
def test_former_names_match_after_calibration(calibrator):
    price, _ = calibrator.feed_ticks()
    assert calibrator.cal.calibration_count() > 0
    cal = calibrator.cal
    for args in [(price, *rest) for _, *rest in ARGUMENTS]:
        assert cal.fair_value(*args) == cal.no_jump_conditional_mean(*args), args
        assert cal.fair_value_quantlib(*args) == cal.no_jump_conditional_mean_quantlib(*args), args
