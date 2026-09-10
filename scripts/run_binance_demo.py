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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import URLError

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from scripts.binance_perps import (
    DEMO_PERPS,
    fetch_funding_snapshot,
    require_demo_perp,
)
from scripts.history import default_data_path, load_price_ticks, resample_last_price, warmup_calibrator
from scripts.merton_runtime import (
    DEFAULT_FUNDING_INTERVAL_HOURS,
    QL_MONITOR_EVERY_N_QUOTES,
    build_calibrator,
    funding_annual,
    horizon_years,
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
    p.add_argument(
        "--symbol",
        default=os.getenv("MERTON_SYMBOL", DEMO_PERPS[0]),
        type=str.upper,
        choices=DEMO_PERPS,
        help="USD-M perpetual (high-volume set)",
    )
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
    p.add_argument("--list-perps", action="store_true", help="Print selectable USD-M perps and exit")
    return p.parse_args(argv)


def ws_url(symbol: str, market: str) -> str:
    stream = f"{symbol.lower()}@bookTicker"
    if market == "spot":
        return f"wss://stream.binance.com:9443/ws/{stream}"
    return f"wss://fstream.binance.com/ws/{stream}"


def fmt_px(price: float) -> str:
    abs_px = abs(price)
    if abs_px >= 100:
        return f"{price:.2f}"
    if abs_px >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}"


def fmt_next_funding(next_ms: Optional[int]) -> str:
    if not next_ms:
        return "n/a"
    dt = datetime.fromtimestamp(next_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


async def run(args: argparse.Namespace) -> None:
    import websockets

    symbol = require_demo_perp(args.symbol)
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
    interval_hours = DEFAULT_FUNDING_INTERVAL_HOURS
    t_years = horizon_years(interval_hours)
    last_funding_ts = 0.0
    quote_count = 0
    url = ws_url(symbol, args.market)
    print(f"perps: {', '.join(DEMO_PERPS)}", file=sys.stderr)
    print(f"connecting {url}", file=sys.stderr)

    async for ws in websockets.connect(url, ping_interval=20):
        try:
            async for raw in ws:
                now = time.time()
                if args.market == "futures" and now - last_funding_ts >= 60:
                    try:
                        snap = fetch_funding_snapshot(symbol)
                        funding_rate = snap.rate
                        interval_hours = snap.interval_hours
                        t_years = horizon_years(interval_hours)
                        last_funding_ts = now
                        print(
                            f"{symbol} funding interval={interval_hours:g}h "
                            f"rate={funding_rate:.8f} next={fmt_next_funding(snap.next_funding_time_ms)}",
                            file=sys.stderr,
                        )
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
                q_annual = (
                    funding_annual(funding_rate, interval_hours) if args.market == "futures" else 0.0
                )
                theo = calibrator.fair_value(mid, q_annual, t_years, 0.0)
                quote_bid, quote_ask = paper_quotes(theo, bid, ask)
                quote_count += 1
                if quote_count % max(args.print_every, 1) != 0:
                    continue
                diff_bps = ((theo - mid) / mid) * 10000 if mid else 0.0
                p = calibrator.params()
                print(
                    f"{symbol} {interval_hours:g}h mid={fmt_px(mid)} theo={fmt_px(theo)} "
                    f"diff={diff_bps:.1f}bps paper=[{fmt_px(quote_bid)}, {fmt_px(quote_ask)}] "
                    f"sigma={p.sigma:.4f} lambda={getattr(p, 'lambda'):.3f}"
                )
                if QL_MONITOR_EVERY_N_QUOTES > 0 and quote_count % QL_MONITOR_EVERY_N_QUOTES == 0:
                    try:
                        theo_ql = calibrator.fair_value_quantlib(mid, q_annual, t_years, 0.0)
                        gap_bps = ((theo_ql - theo) / mid) * 10000 if mid else 0.0
                        print(
                            f"{symbol} ql_monitor fast={fmt_px(theo)} ql={fmt_px(theo_ql)} "
                            f"gap={gap_bps:.2f}bps"
                        )
                    except Exception as exc:
                        print(f"QuantLib monitor failed: {exc}", file=sys.stderr)
        except websockets.ConnectionClosed:
            print("websocket closed; reconnecting", file=sys.stderr)
            continue


def main() -> None:
    args = parse_args()
    if args.list_perps:
        print("\n".join(DEMO_PERPS))
        return
    try:
        require_demo_perp(args.symbol)
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
