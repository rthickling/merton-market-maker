"""Binance USD-M perpetual demo symbols and funding snapshot.

DEMO_PERPS are liquid USDT-margined contracts (high 24h quote volume) including
XAUUSDT so the runner is exercised on a non-8h funding interval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional
from urllib.request import urlopen

from scripts.merton_runtime import DEFAULT_FUNDING_INTERVAL_HOURS

# High-volume USD-M USDT perps. Default demo is BTCUSDT.
DEMO_PERPS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "XAUUSDT",
)

PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
FUNDING_INFO_URL = "https://fapi.binance.com/fapi/v1/fundingInfo"


@dataclass(frozen=True)
class FundingSnapshot:
    rate: float
    mark: Optional[float]
    interval_hours: float
    next_funding_time_ms: Optional[int]


def normalize_perp_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def require_demo_perp(symbol: str) -> str:
    upper = normalize_perp_symbol(symbol)
    if upper not in DEMO_PERPS:
        allowed = ", ".join(DEMO_PERPS)
        raise ValueError(f"symbol must be one of: {allowed} (got {symbol!r})")
    return upper


def fetch_funding_interval_hours(symbol: str, timeout: float = 5.0) -> float:
    """Live settlement frequency from GET /fapi/v1/fundingInfo (1, 4, or 8 hours)."""
    upper = normalize_perp_symbol(symbol)
    with urlopen(FUNDING_INFO_URL, timeout=timeout) as resp:
        rows = json.loads(resp.read().decode())
    for row in rows:
        if row.get("symbol") == upper:
            hours = float(row.get("fundingIntervalHours") or 0.0)
            if hours > 0:
                return hours
    return DEFAULT_FUNDING_INTERVAL_HOURS


def fetch_funding_snapshot(symbol: str, timeout: float = 5.0) -> FundingSnapshot:
    """lastFundingRate is for the current interval, not always 8h."""
    upper = normalize_perp_symbol(symbol)
    prem_url = f"{PREMIUM_INDEX_URL}?symbol={upper}"
    with urlopen(prem_url, timeout=timeout) as resp:
        prem = json.loads(resp.read().decode())
    rate = float(prem.get("lastFundingRate") or 0.0)
    mark_raw = prem.get("markPrice")
    mark = float(mark_raw) if mark_raw else None
    next_ms = prem.get("nextFundingTime")
    interval = fetch_funding_interval_hours(upper, timeout=timeout)
    return FundingSnapshot(
        rate=rate,
        mark=mark,
        interval_hours=interval,
        next_funding_time_ms=int(next_ms) if next_ms else None,
    )
