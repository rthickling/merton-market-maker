"""
Optional ProfitView strategy wrapper around the shared Merton runtime.

Preferred local demo (no ProfitView):
  cd cpp && just demo

To deploy here:
1. Build a Python 3.9 module (`PYTHON_VERSION=3.9.25 just test`) and `just copy-module`.
2. Paste this file into ProfitView's Trading Bots editor (scripts/ must be importable).
3. Subscribe to XBTUSDT and create a BitMEX Market Maker bot.
"""
from __future__ import annotations

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
    paper_quotes,
    tick_calibrator,
)

FUNDING_REFRESH_SEC = 60
SYM = "XBTUSDT"
BITMEX_API_URL = "https://www.bitmex.com/api/v1"


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
        try:
            resp = requests.get(f"{BITMEX_API_URL}/instrument", params={"symbol": SYM}, timeout=5)
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
        mid = (mkt_bid + mkt_ask) / 2
        epoch_ms = int(data.get("time", self.epoch_now))
        try:
            tick_calibrator(self._cpp_calibrator, mid, epoch_ms * 1000)
        except Exception as e:
            logger.error(f"C++ calibrator tick failed: {e}")
            return
        with self._lock:
            q_annual = funding_annual(self._funding_rate)
        theo = self._cpp_calibrator.fair_value(mid, q_annual, T_YEARS, 0.0)
        quote_bid, quote_ask = paper_quotes(theo, mkt_bid, mkt_ask)
        diff_bps = ((theo - mid) / mid) * 10000 if mid else 0
        logger.info(
            f"{sym} mid={mid:.2f} theo={theo:.2f} diff={theo - mid:.2f} ({diff_bps:.1f} bps) "
            f"quote=[{quote_bid:.2f}, {quote_ask:.2f}]"
        )
        self._quote_count += 1
        if QL_MONITOR_EVERY_N_QUOTES > 0 and (self._quote_count % QL_MONITOR_EVERY_N_QUOTES == 0):
            try:
                theo_ql = self._cpp_calibrator.fair_value_quantlib(mid, q_annual, T_YEARS, 0.0)
                gap_bps = ((theo_ql - theo) / mid) * 10000 if mid else 0.0
                logger.info(
                    f"{sym} ql_monitor fast={theo:.2f} ql={theo_ql:.2f} gap={theo_ql-theo:.2f} ({gap_bps:.2f} bps)"
                )
            except Exception as e:
                logger.warning(f"QuantLib monitor failed: {e}")
        self.signal("bitmex", SYM, quote=[quote_bid, quote_ask])
        self.publish(
            "merton_theo",
            {
                "sym": sym,
                "market": mid,
                "theo": theo,
                "diff_bps": diff_bps,
                "quote_bid": quote_bid,
                "quote_ask": quote_ask,
            },
        )
