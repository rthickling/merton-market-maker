#!/usr/bin/env python3
"""Stream Binance bookTicker quotes into the C++ Merton calibrator.

No ProfitView, no order placement. Prints fair value and paper bid/ask.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from scripts.history import default_data_path, load_price_ticks, resample_last_price, warmup_calibrator
from scripts.merton_runtime import (
    QL_MONITOR_EVERY_N_QUOTES,
    T_YEARS,
    build_calibrator,
    funding_annual,
    paper_quotes,
    tick_calibrator,
)


def _import_moc():
    try:
        import merton_online_calibrator as moc
    except ImportError as exc:
        raise SystemExit(
            "Could not import merton_online_calibrator. "
            "Build with `cd cpp && just build` and keep PYTHONPATH on the build directory."
        ) from exc
    return moc


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default=os.getenv("MERTON_SYMBOL", "BTCUSDT"))
    p.add_argument(
        "--market",
        choices=("futures", "spot"),
        default=os.getenv("MERTON_MARKET", "futures"),
    )
    p.add_argument(
        "--data-path",
        default=default_data_path(),
        help="Optional directory/file of CSV or Parquet ticks for warmup",
    )
    p.add_argument("--no-history", action="store_true", help="Skip historical warmup")
    p.add_argument("--print-every", type=int, default=int(os.getenv("MERTON_PRINT_EVERY", "1")))
    return p.parse_args(argv)


def ws_url(symbol: str, market: str) -> str:
    stream = f"{symbol.lower()}@bookTicker"
    if market == "spot":
        return f"wss://stream.binance.com:9443/ws/{stream}"
    return f"wss://fstream.binance.com/ws/{stream}"


def fetch_premium_index(symbol: str) -> tuple[float, Optional[float]]:
    url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol.upper()}"
    with urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read().decode())
    rate = float(data.get("lastFundingRate") or 0.0)
    mark = data.get("markPrice")
    return rate, (float(mark) if mark else None)


async def run(args: argparse.Namespace) -> None:
    import websockets

    moc = _import_moc()
    calibrator = build_calibrator(moc)
    if not args.no_history and args.data_path:
        path = args.data_path
        if not Path(path).expanduser().exists():
            print(f"history path not found ({path}); starting from .env seeds", file=sys.stderr)
        else:
            ticks = resample_last_price(load_price_ticks(path))
            n = warmup_calibrator(calibrator, ticks)
            print(f"warmed calibrator with {n} historical bars from {path}", file=sys.stderr)
    elif not args.no_history:
        print("no MERTON_MARKET_MAKER_DATA_PATH; starting from .env seeds", file=sys.stderr)

    funding_rate = 0.0
    last_funding_ts = 0.0
    quote_count = 0
    url = ws_url(args.symbol, args.market)
    print(f"connecting {url}", file=sys.stderr)

    async for ws in websockets.connect(url, ping_interval=20):
        try:
            async for raw in ws:
                now = time.time()
                if args.market == "futures" and now - last_funding_ts >= 60:
                    try:
                        funding_rate, _mark = fetch_premium_index(args.symbol)
                        last_funding_ts = now
                    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                        print(f"funding refresh failed: {exc}", file=sys.stderr)

                msg: dict[str, Any] = json.loads(raw)
                bid = float(msg.get("b") or 0)
                ask = float(msg.get("a") or 0)
                if not bid or not ask:
                    continue
                mid = (bid + ask) / 2.0
                event_ms = int(msg.get("E") or (now * 1000))
                tick_calibrator(calibrator, mid, event_ms * 1000)
                q_annual = funding_annual(funding_rate) if args.market == "futures" else 0.0
                theo = calibrator.fair_value(mid, q_annual, T_YEARS, 0.0)
                quote_bid, quote_ask = paper_quotes(theo, bid, ask)
                quote_count += 1
                if quote_count % max(args.print_every, 1) != 0:
                    continue
                diff_bps = ((theo - mid) / mid) * 10000 if mid else 0.0
                p = calibrator.params()
                print(
                    f"{args.symbol} mid={mid:.2f} theo={theo:.2f} "
                    f"diff={diff_bps:.1f}bps paper=[{quote_bid:.2f}, {quote_ask:.2f}] "
                    f"sigma={p.sigma:.4f} lambda={getattr(p, 'lambda'):.3f}"
                )
                if QL_MONITOR_EVERY_N_QUOTES > 0 and quote_count % QL_MONITOR_EVERY_N_QUOTES == 0:
                    try:
                        theo_ql = calibrator.fair_value_quantlib(mid, q_annual, T_YEARS, 0.0)
                        gap_bps = ((theo_ql - theo) / mid) * 10000 if mid else 0.0
                        print(f"{args.symbol} ql_monitor fast={theo:.2f} ql={theo_ql:.2f} gap={gap_bps:.2f}bps")
                    except Exception as exc:
                        print(f"QuantLib monitor failed: {exc}", file=sys.stderr)
        except websockets.ConnectionClosed:
            print("websocket closed; reconnecting", file=sys.stderr)
            continue


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
