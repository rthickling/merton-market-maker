"""
Optional ProfitView strategy wrapper around the shared Merton runtime.

Preferred local demo (no ProfitView):
  cd cpp && just demo

To deploy on ProfitView:
1. Build a module (`just test`) and `just copy-module`.
2. Paste this file into ProfitView's Trading Bots editor (scripts/ must be importable).
3. Subscribe to your perpetual symbol and create a Market Maker bot on the connected venue.

Set `MERTON_INSTRUMENT_API_URL` to your venue's REST base for instrument/funding queries
(JSON array with `fundingRate` and optional `markPrice`). Set `PROFITVIEW_SIGNAL_VENUE`
to the venue id ProfitView expects in `signal()`.

The quotes are centred on the market midpoint: mid +/- max(MERTON_MIN_HALF_SPREAD_BPS
of mid, half the market spread). The calibrated parameters and the no-jump conditional
mean over 8h are analytics and do not move them; funding enters only that diagnostic,
as the carry. The `merton_theo` topic keeps its earlier fields: `theo` and `diff_bps`
are aliases of `no_jump_conditional_mean` and `no_jump_mean_vs_mid_bps`, a diagnostic,
not a mispricing. `quote_reference` names the quote centre.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from profitview import Link, logger, cron
import threading
import requests

import merton_online_calibrator as moc
from scripts.merton_runtime import (
    QL_MONITOR_EVERY_N_QUOTES,
    T_YEARS,
    build_calibrator,
    funding_annual,
    midpoint_quotes,
    tick_calibrator,
)

FUNDING_REFRESH_SEC = 60
SYM = os.getenv("MERTON_SYMBOL", "XBTUSDT")
INSTRUMENT_API_URL = os.getenv("MERTON_INSTRUMENT_API_URL", "").rstrip("/")
SIGNAL_VENUE = os.getenv("PROFITVIEW_SIGNAL_VENUE", "")


class Signals(Link):
    def __init__(self, *args, **kwargs):
        self._lock = threading.Lock()
        self._funding_rate = 0.0
        self._mark_price = None
        self._cpp_calibrator = build_calibrator(moc)
        self._quote_count = 0
        super().__init__(*args, **kwargs)

    def on_start(self):
        self.refresh_funding()

    @cron.run(every=FUNDING_REFRESH_SEC)
    def refresh_funding(self):
        if not INSTRUMENT_API_URL:
            logger.warning("MERTON_INSTRUMENT_API_URL is not set; funding carry stays at 0")
            return
        try:
            resp = requests.get(
                f"{INSTRUMENT_API_URL}/instrument",
                params={"symbol": SYM},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return
            inst = data[0]
            with self._lock:
                self._funding_rate = float(inst.get("fundingRate", 0))
                self._mark_price = float(inst.get("markPrice", 0)) if inst.get("markPrice") else None
            logger.info(f"Funding refreshed: rate={self._funding_rate:.6f}, mark={self._mark_price}")
        except Exception as e:
            logger.error(f"Funding refresh failed: {e}")

    def quote_update(self, src: str, sym: str, data: dict):
        if sym != SYM:
            return
        mkt_bid, mkt_ask = data.get("bid", [0, 0])[0], data.get("ask", [0, 0])[0]
        if not mkt_bid or not mkt_ask:
            return
        mid, quote_bid, quote_ask = midpoint_quotes(mkt_bid, mkt_ask)
        epoch_ms = int(data.get("time", self.epoch_now))
        try:
            tick_calibrator(self._cpp_calibrator, mid, epoch_ms * 1000)
        except Exception as e:
            logger.error(f"C++ calibrator tick failed: {e}")
            return
        with self._lock:
            q_annual = funding_annual(self._funding_rate)
        no_jump_mean = self._cpp_calibrator.no_jump_conditional_mean(mid, q_annual, T_YEARS, 0.0)
        vs_mid_bps = ((no_jump_mean - mid) / mid) * 10000 if mid else 0
        logger.info(
            f"{sym} mid={mid:.2f} quote=[{quote_bid:.2f}, {quote_ask:.2f}] "
            f"| diagnostic: no-jump mean over 8h={no_jump_mean:.2f} ({vs_mid_bps:+.1f} bp vs mid)"
        )
        self._quote_count += 1
        if QL_MONITOR_EVERY_N_QUOTES > 0 and (self._quote_count % QL_MONITOR_EVERY_N_QUOTES == 0):
            try:
                ql_mean = self._cpp_calibrator.no_jump_conditional_mean_quantlib(mid, q_annual, T_YEARS, 0.0)
                gap_bps = ((ql_mean - no_jump_mean) / mid) * 10000 if mid else 0.0
                logger.info(
                    f"{sym} ql_monitor (diagnostic) no-jump mean: analytic={no_jump_mean:.2f} "
                    f"quantlib={ql_mean:.2f} (horizon rounded to whole days) gap={gap_bps:.2f}bp"
                )
            except Exception as e:
                logger.warning(f"QuantLib monitor failed: {e}")
        if SIGNAL_VENUE:
            self.signal(SIGNAL_VENUE, SYM, quote=[quote_bid, quote_ask])
        self.publish(
            "merton_theo",
            {
                "sym": sym,
                "market": mid,
                "quote_reference": "mid",
                "quote_bid": quote_bid,
                "quote_ask": quote_ask,
                "no_jump_conditional_mean": no_jump_mean,
                "no_jump_mean_vs_mid_bps": vs_mid_bps,
                # Earlier names of the two diagnostic fields, kept for existing consumers.
                "theo": no_jump_mean,
                "diff_bps": vs_mid_bps,
            },
        )
