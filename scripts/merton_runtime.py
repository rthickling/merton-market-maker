"""Shared C++ calibrator setup and paper-quote helpers.

Used by the local Binance demo, the offline replay and the optional ProfitView
strategy wrapper. Does not import ProfitView. The paper quotes are centred on
the market midpoint. The calibrator estimates continuous volatility and jump
behaviour, and the short-horizon return variance of the fitted parameters sets
how wide the quotes are: the half-spread is the largest of half the market
spread, a fixed floor and a model width (QuotePolicy, model_quote, quote_tick).
This is an illustrative risk-sensitive quoting rule, not an optimal
market-making strategy or a claim of profitability. The no-jump conditional
mean is a separate diagnostic and moves neither the centre nor the width.
"""
from __future__ import annotations

import functools
import math
import os
import time
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional

HOURS_PER_YEAR = 365.25 * 24
SECONDS_PER_YEAR = HOURS_PER_YEAR * 3600.0
DEFAULT_FUNDING_INTERVAL_HOURS = 8.0
T_HOURS = DEFAULT_FUNDING_INTERVAL_HOURS  # one full 8h funding interval: a fixed horizon, not a countdown
T_YEARS = T_HOURS / HOURS_PER_YEAR
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


def no_jump_conditional_mean(
    S0: float,
    sigma: float,
    lam: float,
    mu_j: float,
    delta_j: float,
    q_annual: float,
    T_years: float,
    r: float = 0.0,
) -> float:
    """E[S_T | no jumps in (0, T]] = S0*exp((r - q - λk)*T), k = exp(μ_J + δ_J²/2) - 1.

    Same as OnlineMertonCalibrator.no_jump_conditional_mean. A diagnostic, not a
    fair value to quote around.
    """
    k = math.exp(mu_j + 0.5 * delta_j**2) - 1
    drift = r - q_annual - lam * k
    return S0 * math.exp(drift * T_years)


merton_theoretical = no_jump_conditional_mean  # former name, kept for existing callers


def horizon_years(interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS) -> float:
    """Horizon T for the no-jump diagnostic: the full length of one funding interval.

    The length is fixed. It is not the time left until the next funding payment,
    and funding payments do not reset the price.
    """
    hours = float(interval_hours)
    if hours <= 0:
        raise ValueError("funding interval hours must be positive")
    return hours / HOURS_PER_YEAR


def funding_annual(
    rate_per_interval: float,
    interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS,
) -> float:
    """Annualize a per-interval funding rate (Binance lastFundingRate is per current interval).

    The demos use it as context: the carry q in the no-jump diagnostic. The quotes do not use it.
    """
    hours = float(interval_hours)
    if hours <= 0:
        raise ValueError("funding interval hours must be positive")
    return float(rate_per_interval) * (HOURS_PER_YEAR / hours)


def _accepts_theo_keyword(fn):
    """Keep paper_quotes(theo=...) working: theo was reference_price's former name."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if "theo" in kwargs:
            if "reference_price" in kwargs:
                raise TypeError(f"{fn.__name__}() got both reference_price and its former name, theo")
            kwargs["reference_price"] = kwargs.pop("theo")
        return fn(*args, **kwargs)

    return wrapper


@_accepts_theo_keyword
def paper_quotes(
    reference_price: float, mkt_bid: float, mkt_ask: float, min_half_spread_bps: float = MIN_HALF_SPREAD_BPS
) -> tuple[float, float]:
    """Symmetric paper bid and ask around reference_price.

    The half-spread is the larger of min_half_spread_bps of reference_price and
    half the market spread, with no model term. Kept for existing callers; the
    demos quote with quote_tick, which adds the model width. The former keyword,
    theo, is still accepted.
    """
    min_half = reference_price * (min_half_spread_bps / 10000.0)
    mkt_half = max((mkt_ask - mkt_bid) / 2.0, 0.0)
    half_spread = max(min_half, mkt_half)
    return reference_price - half_spread, reference_price + half_spread


def midpoint_quotes(
    mkt_bid: float, mkt_ask: float, min_half_spread_bps: float = MIN_HALF_SPREAD_BPS
) -> tuple[float, float, float]:
    """(mid, paper bid, paper ask): paper_quotes centred on the market midpoint, without the model width."""
    mid = (mkt_bid + mkt_ask) / 2.0
    quote_bid, quote_ask = paper_quotes(mid, mkt_bid, mkt_ask, min_half_spread_bps)
    return mid, quote_bid, quote_ask


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


class QuoteError(ValueError):
    """No usable paper quote for this update. Callers skip the update; no fallback quote replaces it."""


_POLICY_ENV = {
    "risk_horizon_seconds": "MERTON_RISK_HORIZON_SECONDS",
    "risk_multiplier": "MERTON_RISK_MULTIPLIER",
    "min_half_spread_bps": "MERTON_MIN_HALF_SPREAD_BPS",
}


def _check_policy_setting(label: str, value: float, zero_allowed: bool) -> None:
    if not (math.isfinite(value) and (value >= 0 if zero_allowed else value > 0)):
        bound = ">= 0" if zero_allowed else "> 0"
        raise ValueError(f"{label} must be finite and {bound}, got {value!r}")


@dataclass(frozen=True)
class QuotePolicy:
    """Settings for the paper-quote width. The defaults are demonstration choices.

    risk_horizon_seconds is the exposure horizon the model width is computed
    over. It is chosen for this rule and is independent of the funding interval
    and of when the calibrator refits.
    """

    risk_horizon_seconds: float = 60.0
    risk_multiplier: float = 1.0
    min_half_spread_bps: float = 2.0

    def __post_init__(self) -> None:
        for name in _POLICY_ENV:
            _check_policy_setting(name, getattr(self, name), zero_allowed=name == "min_half_spread_bps")

    @classmethod
    def from_env(cls) -> QuotePolicy:
        """Read MERTON_RISK_HORIZON_SECONDS, MERTON_RISK_MULTIPLIER and MERTON_MIN_HALF_SPREAD_BPS.

        An unset or empty variable keeps the default. Invalid values raise ValueError naming the variable.
        """
        values = {}
        for name, var in _POLICY_ENV.items():
            raw = os.getenv(var, "").strip()
            if raw:
                try:
                    value = float(raw)
                except ValueError:
                    raise ValueError(f"{var} must be a number, got {raw!r}") from None
                _check_policy_setting(var, value, zero_allowed=name == "min_half_spread_bps")
                values[name] = value
        return cls(**values)

    @property
    def risk_horizon_years(self) -> float:
        return self.risk_horizon_seconds / SECONDS_PER_YEAR


class ParamSnapshot(NamedTuple):
    """The calibrator's parameters at one moment: what a quote is computed from."""

    sigma: float
    lam: float
    mu_j: float
    delta_j: float

    def describe(self) -> str:
        return f"sigma={self.sigma:.4f} lambda={self.lam:.3f} mu_j={self.mu_j:.6f} delta_j={self.delta_j:.6f}"


def log_return_variance(params: ParamSnapshot, t_years: float) -> float:
    """Var[ln(S_T / S_0)] = T(σ² + λ(μ_J² + δ_J²)) under the Merton model the calibrator fits.

    λ(μ_J² + δ_J²) is the jump contribution. The summed log jumps over T are a
    compound Poisson sum with variance λT·E[Y²] = λT(δ_J² + μ_J²): the mean jump
    size counts as well as its spread, because the number of jumps is random.
    """
    p = params
    # Products rather than **: an overflowing float ** raises, an overflowing product gives inf.
    return t_years * (p.sigma * p.sigma + p.lam * (p.mu_j * p.mu_j + p.delta_j * p.delta_j))


@dataclass(frozen=True)
class ModelQuote:
    """A paper quote centred on the market midpoint, with what set its width.

    Half-spreads are in price units; bps() converts one to basis points of mid.
    """

    mid: float
    bid: float
    ask: float
    half_spread: float
    market_half_spread: float
    floor_half_spread: float
    model_half_spread: float
    set_by: str  # "market spread", "floor" or "model"
    params_source: str  # "seeded" until the first calibration, then "calibrated"
    risk_horizon_seconds: float
    params: ParamSnapshot

    def bps(self, half_spread: float) -> float:
        return half_spread / self.mid * 10_000.0

    def describe(self) -> str:
        """E.g. half=7.35bp set by model (model 7.35bp, floor 2.00bp, market 0.00bp; 60s horizon; params calibrated)."""
        return (
            f"half={self.bps(self.half_spread):.2f}bp set by {self.set_by} "
            f"(model {self.bps(self.model_half_spread):.2f}bp, floor {self.bps(self.floor_half_spread):.2f}bp, "
            f"market {self.bps(self.market_half_spread):.2f}bp; {self.risk_horizon_seconds:g}s horizon; "
            f"params {self.params_source})"
        )


def _check_book(mkt_bid: float, mkt_ask: float) -> None:
    for side, price in (("bid", mkt_bid), ("ask", mkt_ask)):
        if not (math.isfinite(price) and price > 0):
            raise QuoteError(f"quote rejected: market {side} {price!r} is not a positive finite price")
    if mkt_bid > mkt_ask:
        raise QuoteError(f"quote rejected: crossed book, bid {mkt_bid!r} > ask {mkt_ask!r}")


def model_quote(
    mkt_bid: float, mkt_ask: float, params: ParamSnapshot, policy: QuotePolicy, *, calibrated: bool
) -> ModelQuote:
    """Paper quote: mid ± max(half the market spread, floor, model width).

    model width = mid × risk_multiplier × sqrt(log_return_variance over the risk
    horizon). That converts a log-return dispersion into a price distance, a
    reasonable approximation over short horizons. It is not an exact price
    standard deviation, a confidence interval or a VaR, and it predicts no
    direction. On a tie the market spread wins, then the floor.

    Raises QuoteError for a bad book, unusable parameters or an unusable quote.
    """
    _check_book(mkt_bid, mkt_ask)
    for name, value in zip(params._fields, params):
        if not math.isfinite(value):
            raise QuoteError(f"quote rejected: {name}={value!r} is not finite")
    for name in ("sigma", "lam", "delta_j"):
        value = getattr(params, name)
        if value < 0:
            raise QuoteError(f"quote rejected: {name}={value!r} is negative")
    variance = log_return_variance(params, policy.risk_horizon_years)
    if not math.isfinite(variance):
        raise QuoteError(f"quote rejected: log-return variance {variance!r} is not finite ({params.describe()})")

    mid = (mkt_bid + mkt_ask) / 2.0
    candidates = (
        ("market spread", (mkt_ask - mkt_bid) / 2.0),
        ("floor", mid * policy.min_half_spread_bps / 10_000.0),
        ("model", mid * policy.risk_multiplier * math.sqrt(variance)),
    )
    set_by, half_spread = max(candidates, key=lambda candidate: candidate[1])
    bid, ask = mid - half_spread, mid + half_spread
    if not (math.isfinite(bid) and math.isfinite(ask)):
        raise QuoteError(f"quote rejected: quote [{bid!r}, {ask!r}] is not finite")
    if bid <= 0:
        raise QuoteError(f"quote rejected: bid {bid!r} is not positive (half-spread {half_spread!r} at mid {mid!r})")
    if not ask > bid:
        raise QuoteError(f"quote rejected: zero-width quote at mid {mid!r}")
    return ModelQuote(
        mid=mid,
        bid=bid,
        ask=ask,
        half_spread=half_spread,
        market_half_spread=candidates[0][1],
        floor_half_spread=candidates[1][1],
        model_half_spread=candidates[2][1],
        set_by=set_by,
        params_source="calibrated" if calibrated else "seeded",
        risk_horizon_seconds=policy.risk_horizon_seconds,
        params=params,
    )


def quote_tick(
    calibrator: Any, mkt_bid: float, mkt_ask: float, epoch_us: int, policy: QuotePolicy
) -> tuple[TickResult, ModelQuote]:
    """Feed the midpoint to the calibrator, then quote from the parameters it holds afterwards.

    The calibration step comes first, so a refit on this tick sets this tick's
    width; between refits the latest fit applies. Until the first calibration
    the parameters are the seeds, and the quote says so. A bad book raises
    QuoteError before the calibrator sees it.
    """
    _check_book(mkt_bid, mkt_ask)
    tick = tick_calibrator(calibrator, (mkt_bid + mkt_ask) / 2.0, epoch_us)
    params = ParamSnapshot(tick.sigma, tick.lam, tick.mu_j, tick.delta_j)
    calibrated = calibrator.calibration_count() > 0
    return tick, model_quote(mkt_bid, mkt_ask, params, policy, calibrated=calibrated)


def quote_payload(sym: str, quote: ModelQuote, no_jump_mean: float, vs_mid_bps: float) -> dict[str, Any]:
    """The ProfitView merton_theo topic: the quote, what set its width, and the no-jump diagnostic."""
    return {
        "sym": sym,
        "market": quote.mid,
        "quote_reference": "mid",
        "quote_bid": quote.bid,
        "quote_ask": quote.ask,
        "half_spread_bps": quote.bps(quote.half_spread),
        "model_half_spread_bps": quote.bps(quote.model_half_spread),
        "floor_half_spread_bps": quote.bps(quote.floor_half_spread),
        "market_half_spread_bps": quote.bps(quote.market_half_spread),
        "width_set_by": quote.set_by,
        "params_source": quote.params_source,
        "risk_horizon_seconds": quote.risk_horizon_seconds,
        "no_jump_conditional_mean": no_jump_mean,
        "no_jump_mean_vs_mid_bps": vs_mid_bps,
        # Earlier names of the two diagnostic fields, kept for existing consumers.
        "theo": no_jump_mean,
        "diff_bps": vs_mid_bps,
    }


class LogThrottle:
    """Lets through at most one line per `seconds`; 0 lets every line through."""

    def __init__(self, seconds: float) -> None:
        if not (math.isfinite(seconds) and seconds >= 0):
            raise ValueError(f"log interval must be finite and >= 0 seconds, got {seconds!r}")
        self.seconds = seconds
        self._last: Optional[float] = None

    def ready(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        if self._last is not None and now - self._last < self.seconds:
            return False
        self._last = now
        return True
