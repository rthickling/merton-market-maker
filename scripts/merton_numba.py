"""The pure-Python reference calibrator with its likelihood compiled by Numba.

OnlineMertonCalibrator here is the class from scripts/merton_reference.py with
one method replaced: the negative log-likelihood, where nearly all the time
goes, is computed by neg_log_likelihood below, which Numba compiles to machine
code. Ticks, gating, the median time step, the coordinate search and
no_jump_conditional_mean (with its former name, fair_value) stay in Python.
The kernel is the reference's arithmetic in the same order; the parameters
arrive as floats and the return window as a NumPy array, because compiled code
cannot take the dataclass or the deque.

Plain @njit: no fastmath, no parallel, no on-disk cache. Numba compiles on the
first call in each process; warm_up() does that up front.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from numba import njit

from scripts import merton_reference as ref
from scripts.merton_reference import INV_SQRT_2PI, CalibratorConfig, MertonParams


@njit
def _max(a, b):
    return b if a < b else a


@njit
def _safe_log(x):
    return math.log(_max(x, 1e-300))


@njit
def _jump_compensator(mu_j, delta_j):
    return math.exp(mu_j + 0.5 * delta_j * delta_j) - 1.0


@njit
def _standard_normal_pdf(z):
    return INV_SQRT_2PI * math.exp(-0.5 * z * z)


@njit
def _poisson_weight(n, lambda_dt):
    if n == 0:
        return math.exp(-lambda_dt)
    w = math.exp(-lambda_dt)
    for i in range(1, n + 1):
        w *= lambda_dt / i
    return w


@njit
def _merton_pdf(x, sigma, lambda_, mu_j, delta_j, dt_years, n_max):
    lambda_dt = lambda_ * dt_years
    k = _jump_compensator(mu_j, delta_j)
    drift = (-lambda_ * k - 0.5 * sigma * sigma) * dt_years

    pdf = 0.0
    for n in range(n_max):
        mu_n = drift + n * mu_j
        var_n = sigma * sigma * dt_years + n * delta_j * delta_j
        if var_n <= 0.0:
            continue
        sigma_n = math.sqrt(var_n)
        z = (x - mu_n) / sigma_n
        pdf += _poisson_weight(n, lambda_dt) * (_standard_normal_pdf(z) / sigma_n)
    return _max(pdf, 1e-300)


@njit
def neg_log_likelihood(returns, sigma, lambda_, mu_j, delta_j, dt_years, n_max):
    if not sigma > 0.0 or not lambda_ >= 0.0 or not delta_j > 0.0:
        return math.inf
    nll = 0.0
    for r in returns:
        nll -= _safe_log(_merton_pdf(r, sigma, lambda_, mu_j, delta_j, dt_years, n_max))
    return nll


def warm_up() -> None:
    """Compile neg_log_likelihood for the argument types the calibrator passes."""
    neg_log_likelihood(np.zeros(1), 0.44, 20.0, 0.003, 0.01, 1e-4, CalibratorConfig().n_max)


class OnlineMertonCalibrator(ref.OnlineMertonCalibrator):
    def __init__(self, initial: MertonParams, config: Optional[CalibratorConfig] = None) -> None:
        super().__init__(initial, config)
        self._window: Optional[np.ndarray] = None

    def update_tick(self, price: float, epoch_us: int) -> bool:
        accepted = super().update_tick(price, epoch_us)
        if accepted:
            self._window = None
        return accepted

    def _neg_log_likelihood(self, p: MertonParams, dt_years: float) -> float:
        # One calibration evaluates the likelihood 1 + 8 * coordinate_steps times on the same window.
        if self._window is None:
            self._window = np.fromiter(self._returns, dtype=np.float64, count=len(self._returns))
        return neg_log_likelihood(self._window, p.sigma, p.lambda_, p.mu_j, p.delta_j, dt_years, self._config.n_max)
