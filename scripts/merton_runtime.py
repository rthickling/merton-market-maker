"""Shared C++ calibrator setup and paper-quote helpers.

Used by the local Binance demo and the optional ProfitView strategy wrapper.
Does not import ProfitView.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Optional

T_HOURS = 8
T_YEARS = T_HOURS / (365.25 * 24)
MIN_HALF_SPREAD_BPS = float(os.getenv("MERTON_MIN_HALF_SPREAD_BPS", "2.0"))
QL_MONITOR_EVERY_N_QUOTES = int(os.getenv("MERTON_QL_MONITOR_EVERY_N_QUOTES", "120"))

CPP_WINDOW_SIZE = 4096
CPP_MIN_POINTS_FOR_UPDATE = 512
CPP_UPDATE_EVERY_N_RETURNS = 128
CPP_N_MAX = 15
CPP_COORDINATE_STEPS = 3


def seed_params_from_env() -> dict[str, float]:
    return {
        "sigma": float(os.getenv("MERTON_SIGMA", "0.44")),
        "lambda": float(os.getenv("MERTON_LAMBDA", "20.0")),
        "mu_j": float(os.getenv("MERTON_MU_J", "0.003")),
        "delta_j": float(os.getenv("MERTON_DELTA_J", "0.01")),
    }


def merton_theoretical(
    S0: float,
    sigma: float,
    lam: float,
    mu_j: float,
    delta_j: float,
    q_annual: float,
    T_years: float,
    r: float = 0.0,
) -> float:
    """E[S_T] = S_0 * exp((r - q - λk) * T), k = exp(μ_J + δ_J²/2) - 1"""
    k = math.exp(mu_j + 0.5 * delta_j**2) - 1
    drift = r - q_annual - lam * k
    return S0 * math.exp(drift * T_years)


def funding_annual(rate_per_8h: float) -> float:
    """Convert a per-8h funding rate to annualized (simple, matching live path)."""
    return rate_per_8h * (365.25 * 24 / 8)


def paper_quotes(theo: float, mkt_bid: float, mkt_ask: float, min_half_spread_bps: float = MIN_HALF_SPREAD_BPS) -> tuple[float, float]:
    min_half = theo * (min_half_spread_bps / 10000.0)
    mkt_half = max((mkt_ask - mkt_bid) / 2.0, 0.0)
    half_spread = max(min_half, mkt_half)
    return theo - half_spread, theo + half_spread


def build_calibrator(moc: Any, seeds: Optional[dict[str, float]] = None) -> Any:
    seeds = seeds or seed_params_from_env()
    p = moc.MertonParams()
    p.sigma = seeds["sigma"]
    setattr(p, "lambda", seeds["lambda"])
    p.mu_j = seeds["mu_j"]
    p.delta_j = seeds["delta_j"]

    cfg = moc.CalibratorConfig()
    cfg.window_size = CPP_WINDOW_SIZE
    cfg.min_points_for_update = CPP_MIN_POINTS_FOR_UPDATE
    cfg.update_every_n_returns = CPP_UPDATE_EVERY_N_RETURNS
    cfg.n_max = CPP_N_MAX
    cfg.coordinate_steps = CPP_COORDINATE_STEPS
    return moc.OnlineMertonCalibrator(p, cfg)


@dataclass
class TickResult:
    accepted: bool
    params_updated: bool
    sigma: float
    lam: float
    mu_j: float
    delta_j: float


def tick_calibrator(calibrator: Any, price: float, epoch_us: int) -> TickResult:
    p0 = calibrator.params()
    result = TickResult(
        accepted=False,
        params_updated=False,
        sigma=float(p0.sigma),
        lam=float(getattr(p0, "lambda")),
        mu_j=float(p0.mu_j),
        delta_j=float(p0.delta_j),
    )
    if not price:
        return result
    accepted = calibrator.update_tick(float(price), int(epoch_us))
    result.accepted = bool(accepted)
    if accepted and calibrator.maybe_update_params():
        p = calibrator.params()
        result.params_updated = True
        result.sigma = float(p.sigma)
        result.lam = float(getattr(p, "lambda"))
        result.mu_j = float(p.mu_j)
        result.delta_j = float(p.delta_j)
    return result
