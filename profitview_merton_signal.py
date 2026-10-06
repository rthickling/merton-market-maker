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

The quotes are centred on the market midpoint. Their half-spread is the largest of half
the market spread, MERTON_MIN_HALF_SPREAD_BPS of mid, and mid x MERTON_RISK_MULTIPLIER x
the fitted model's log-return standard deviation over MERTON_RISK_HORIZON_SECONDS
(scripts/merton_runtime.py). Each update reaches the calibrator before it is quoted.
An update that cannot be quoted is logged as an error and sends no signal and publishes
nothing. Quote and rejection log lines are throttled to one each per MERTON_PRINT_SECONDS
(default 1), and the QuantLib monitor line to one per ten times that; parameter updates
are always logged.

The no-jump conditional mean over 8h is a diagnostic and moves neither the centre nor
the width; funding enters only that diagnostic, as the carry. The `merton_theo` topic
keeps its earlier fields: `theo` and `diff_bps` are aliases of `no_jump_conditional_mean`
and `no_jump_mean_vs_mid_bps`, a diagnostic, not a mispricing, and `quote_reference`
names the quote centre. It adds what set the width: `half_spread_bps`,
`model_half_spread_bps`, `floor_half_spread_bps`, `market_half_spread_bps`,
`width_set_by`, `params_source` and `risk_horizon_seconds`.
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
    LogThrottle,
    QuoteError,
    QuotePolicy,
    build_calibrator,
    funding_annual,
    quote_payload,
    quote_tick,
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
        self._policy = QuotePolicy.from_env()
        log_seconds = float(os.getenv("MERTON_PRINT_SECONDS", "1"))
        self._quote_log = LogThrottle(log_seconds)
        self._rejection_log = LogThrottle(log_seconds)
        self._monitor_log = LogThrottle(10 * log_seconds)
        self._rejected = 0
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
        epoch_ms = int(data.get("time", self.epoch_now))
        try:
            tick, quote = quote_tick(
                self._cpp_calibrator, float(mkt_bid), float(mkt_ask), epoch_ms * 1000, self._policy
            )
        except QuoteError as e:
            self._rejected += 1
            if self._rejection_log.ready():
                logger.error(f"{sym} {e}; no signal or publish for this update ({self._rejected} rejected so far)")
            return
        except Exception as e:
            logger.error(f"C++ calibrator tick failed: {e}")
            return
        with self._lock:
            q_annual = funding_annual(self._funding_rate)
        no_jump_mean = self._cpp_calibrator.no_jump_conditional_mean(quote.mid, q_annual, T_YEARS, 0.0)
        vs_mid_bps = ((no_jump_mean - quote.mid) / quote.mid) * 10000
        if tick.params_updated:
            logger.info(
                f"{sym} params updated (calibration {self._cpp_calibrator.calibration_count()}): "
                f"{quote.params.describe()} -> model half-spread {quote.bps(quote.model_half_spread):.2f}bp"
            )
        if self._quote_log.ready():
            logger.info(
                f"{sym} mid={quote.mid:.2f} quote=[{quote.bid:.2f}, {quote.ask:.2f}] {quote.describe()} "
                f"| diagnostic, not used by quotes: no-jump mean over 8h={no_jump_mean:.2f} "
                f"({vs_mid_bps:+.1f} bp vs mid)"
            )
        self._quote_count += 1
        if (
            QL_MONITOR_EVERY_N_QUOTES > 0
            and self._quote_count % QL_MONITOR_EVERY_N_QUOTES == 0
            and self._monitor_log.ready()
        ):
            try:
                ql_mean = self._cpp_calibrator.no_jump_conditional_mean_quantlib(quote.mid, q_annual, T_YEARS, 0.0)
                gap_bps = ((ql_mean - no_jump_mean) / quote.mid) * 10000
                logger.info(
                    f"{sym} ql_monitor (diagnostic) no-jump mean: analytic={no_jump_mean:.2f} "
                    f"quantlib={ql_mean:.2f} (horizon rounded to whole days) gap={gap_bps:.2f}bp"
                )
            except Exception as e:
                logger.warning(f"QuantLib monitor failed: {e}")
        if SIGNAL_VENUE:
            self.signal(SIGNAL_VENUE, SYM, quote=[quote.bid, quote.ask])
        self.publish("merton_theo", quote_payload(sym, quote, no_jump_mean, vs_mid_bps))
