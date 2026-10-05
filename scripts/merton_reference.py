"""Readable pure-Python translation of the C++ OnlineMertonCalibrator.

It follows cpp/src/merton_online_calibrator.cpp operation by operation: the
same API, gate, loop order, clamps, median time step and Poisson sum. That
makes the two comparable number for number, and timeable doing identical work.
Agreement between them shows the implementations are consistent with each
other; it does not validate the Merton model or its calibration.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace
from typing import Optional

SECS_PER_YEAR = 365.25 * 24.0 * 3600.0
INV_SQRT_2PI = 0.3989422804014326779399460599343818684759


def _max(a: float, b: float) -> float:
    """std::max(a, b)."""
    return b if a < b else a


def _clamp(v: float, lo: float, hi: float) -> float:
    """std::clamp(v, lo, hi)."""
    if v < lo:
        return lo
    if hi < v:
        return hi
    return v


def safe_log(x: float) -> float:
    return math.log(_max(x, 1e-300))


def jump_compensator(mu_j: float, delta_j: float) -> float:
    """k = E[J - 1] = exp(mu_j + delta_j^2 / 2) - 1."""
    return math.exp(mu_j + 0.5 * delta_j * delta_j) - 1.0


def standard_normal_pdf(z: float) -> float:
    return INV_SQRT_2PI * math.exp(-0.5 * z * z)


def poisson_weight(n: int, lambda_dt: float) -> float:
    """exp(-lambda_dt) * lambda_dt^n / n!, built up term by term."""
    if n == 0:
        return math.exp(-lambda_dt)
    w = math.exp(-lambda_dt)
    for i in range(1, n + 1):
        w *= lambda_dt / i
    return w


@dataclass
class MertonParams:
    sigma: float = 0.44
    lambda_: float = 20.0
    mu_j: float = 0.003
    delta_j: float = 0.01


@dataclass
class CalibratorConfig:
    window_size: int = 4096
    min_points_for_update: int = 512
    n_max: int = 15
    update_every_n_returns: int = 128
    coordinate_steps: int = 3
    improvement_tol: float = 1e-6


class OnlineMertonCalibrator:
    def __init__(self, initial: MertonParams, config: Optional[CalibratorConfig] = None) -> None:
        self._config = config if config is not None else CalibratorConfig()
        self._params = self._clamp_params(initial)
        self._last_price: Optional[float] = None
        self._last_ts_us: Optional[int] = None
        self._returns: deque[float] = deque()
        self._dt_us: deque[int] = deque()
        self._returns_since_last_update = 0
        self._calibration_count = 0

    # --- public API (mirrors the C++ class) ---------------------------------

    def update_tick(self, price: float, epoch_us: int) -> bool:
        if not price > 0.0:
            return False
        if self._last_price is None or self._last_ts_us is None:
            self._last_price = price
            self._last_ts_us = epoch_us
            return False

        dt_us = epoch_us - self._last_ts_us
        if dt_us <= 0:
            self._last_price = price
            self._last_ts_us = epoch_us
            return False

        r = math.log(price / self._last_price)
        if not math.isfinite(r):
            self._last_price = price
            self._last_ts_us = epoch_us
            return False

        self._returns.append(r)
        self._dt_us.append(dt_us)
        if len(self._returns) > self._config.window_size:
            self._returns.popleft()
            self._dt_us.popleft()

        self._returns_since_last_update += 1
        self._last_price = price
        self._last_ts_us = epoch_us
        return True

    def maybe_update_params(self) -> bool:
        cfg = self._config
        if len(self._returns) < cfg.min_points_for_update:
            return False
        if self._returns_since_last_update < cfg.update_every_n_returns:
            return False

        self._returns_since_last_update = 0
        dt = self._estimate_dt_years()
        if not dt > 0.0:
            return False
        self._calibration_count += 1

        best = self._params
        best_nll = self._neg_log_likelihood(best, dt)

        # Adaptive step sizes: a fraction of each parameter, with floors.
        step = MertonParams(
            sigma=_max(0.02, best.sigma * 0.08),
            lambda_=_max(0.10, best.lambda_ * 0.10),
            mu_j=_max(0.002, abs(best.mu_j) * 0.25),
            delta_j=_max(0.002, best.delta_j * 0.20),
        )

        for _ in range(cfg.coordinate_steps):
            improved = False

            def try_param(candidate: MertonParams) -> None:
                nonlocal best, best_nll, improved
                c = self._clamp_params(candidate)
                nll = self._neg_log_likelihood(c, dt)
                if math.isfinite(nll) and (best_nll - nll) > cfg.improvement_tol:
                    best = c
                    best_nll = nll
                    improved = True

            try_param(replace(best, sigma=best.sigma + step.sigma))
            try_param(replace(best, sigma=best.sigma - step.sigma))

            try_param(replace(best, lambda_=best.lambda_ + step.lambda_))
            try_param(replace(best, lambda_=best.lambda_ - step.lambda_))

            try_param(replace(best, mu_j=best.mu_j + step.mu_j))
            try_param(replace(best, mu_j=best.mu_j - step.mu_j))

            try_param(replace(best, delta_j=best.delta_j + step.delta_j))
            try_param(replace(best, delta_j=best.delta_j - step.delta_j))

            # Shrink the steps after a round without improvement.
            if not improved:
                step = MertonParams(
                    sigma=step.sigma * 0.5,
                    lambda_=step.lambda_ * 0.5,
                    mu_j=step.mu_j * 0.5,
                    delta_j=step.delta_j * 0.5,
                )

        p = self._params
        changed = (
            abs(best.sigma - p.sigma) > 1e-12
            or abs(best.lambda_ - p.lambda_) > 1e-12
            or abs(best.mu_j - p.mu_j) > 1e-12
            or abs(best.delta_j - p.delta_j) > 1e-12
        )
        self._params = best
        return changed

    def no_jump_conditional_mean(self, s0: float, q_annual: float, t_years: float, r: float) -> float:
        """E[S_T | no jumps in (0, T]] = S0*exp((r - q - λκ)T), as in the C++ method.

        Not the unconditional E[S_T], and not a fair value to quote around.
        """
        p = self._params
        k = jump_compensator(p.mu_j, p.delta_j)
        drift = r - q_annual - p.lambda_ * k
        return s0 * math.exp(drift * t_years)

    def fair_value(self, s0: float, q_annual: float, t_years: float, r: float) -> float:
        """Former name of no_jump_conditional_mean, kept for existing callers: same arguments, same result."""
        return self.no_jump_conditional_mean(s0, q_annual, t_years, r)

    def params(self) -> MertonParams:
        return replace(self._params)

    def sample_count(self) -> int:
        return len(self._returns)

    def calibration_count(self) -> int:
        return self._calibration_count

    # --- internals ------------------------------------------------------------

    def _merton_pdf(self, x: float, p: MertonParams, dt_years: float) -> float:
        """Poisson-weighted mixture of normals, truncated at n_max terms."""
        lambda_dt = p.lambda_ * dt_years
        k = jump_compensator(p.mu_j, p.delta_j)
        drift = (-p.lambda_ * k - 0.5 * p.sigma * p.sigma) * dt_years

        pdf = 0.0
        for n in range(self._config.n_max):
            mu_n = drift + n * p.mu_j
            var_n = p.sigma * p.sigma * dt_years + n * p.delta_j * p.delta_j
            if var_n <= 0.0:
                continue
            sigma_n = math.sqrt(var_n)
            z = (x - mu_n) / sigma_n
            pdf += poisson_weight(n, lambda_dt) * (standard_normal_pdf(z) / sigma_n)
        return _max(pdf, 1e-300)

    def _neg_log_likelihood(self, p: MertonParams, dt_years: float) -> float:
        if not p.sigma > 0.0 or not p.lambda_ >= 0.0 or not p.delta_j > 0.0:
            return math.inf
        nll = 0.0
        for r in self._returns:
            nll -= safe_log(self._merton_pdf(r, p, dt_years))
        return nll

    @staticmethod
    def _clamp_params(p: MertonParams) -> MertonParams:
        return MertonParams(
            sigma=_clamp(p.sigma, 0.05, 3.0),
            lambda_=_clamp(p.lambda_, 0.01, 40.0),
            mu_j=_clamp(p.mu_j, -0.5, 0.5),
            delta_j=_clamp(p.delta_j, 0.01, 1.0),
        )

    def _estimate_dt_years(self) -> float:
        if not self._dt_us:
            return 0.0
        s = sorted(self._dt_us)
        median_us = s[len(s) // 2]
        return median_us / 1e6 / SECS_PER_YEAR
