#!/usr/bin/env python3
"""Stream Binance bookTicker quotes into the C++ Merton calibrator.

No ProfitView, no order placement. Prints illustrative paper quotes centred on
the market midpoint, with a half-spread that is the largest of half the market
spread, a floor and a model width from the fitted parameters' short-horizon
return variance (scripts/merton_runtime.py). Each update is fed to the
calibrator before it is quoted, so a refit sets the width of the update that
produced it. A periodic line shows, as a diagnostic the quotes do not use, the
no-jump conditional mean over one funding interval.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
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
    LogThrottle,
    QuoteError,
    QuotePolicy,
    build_calibrator,
    funding_annual,
    horizon_years,
    quote_tick,
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


def non_negative_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value >= 0):
        raise argparse.ArgumentTypeError(f"need a finite number of seconds >= 0, got {text!r}")
    return value


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
    p.add_argument(
        "--print-seconds",
        type=non_negative_seconds,
        default=os.getenv("MERTON_PRINT_SECONDS", "1"),
        help="At most one quote line and one rejection line per this many seconds, and one no-jump diagnostic "
        "line per ten times as many (0: no limit). Parameter updates always print. "
        "Default: MERTON_PRINT_SECONDS, else 1",
    )
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


async def run(args: argparse.Namespace, policy: QuotePolicy) -> None:
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
    rejected = 0
    quote_lines = LogThrottle(args.print_seconds)
    rejection_lines = LogThrottle(args.print_seconds)
    diagnostic_lines = LogThrottle(10 * args.print_seconds)
    url = ws_url(symbol, args.market)
    print(f"perps: {', '.join(DEMO_PERPS)}", file=sys.stderr)
    print(
        "paper quotes: mid +/- the largest of half the market spread, "
        f"{policy.min_half_spread_bps:g} bp of mid, and {policy.risk_multiplier:g} x mid x the fitted model's "
        f"{policy.risk_horizon_seconds:g}s log-return standard deviation; no orders are placed. "
        "The no-jump mean is a diagnostic and does not move the quotes.",
        file=sys.stderr,
    )
    print(f"connecting {url}", file=sys.stderr)

    # Binance can leave a closing connection open for the whole default close_timeout (10 s), which delays Ctrl-C.
    async for ws in websockets.connect(url, ping_interval=20, close_timeout=1):
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
                            f"{symbol} funding (context) interval={interval_hours:g}h "
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
                event_ms = int(msg.get("E") or (now * 1000))
                try:
                    tick, quote = quote_tick(calibrator, bid, ask, event_ms * 1000, policy)
                except QuoteError as exc:
                    rejected += 1
                    if rejection_lines.ready():
                        print(f"{symbol} {exc}; no quote for this update ({rejected} rejected so far)", file=sys.stderr)
                    continue
                quote_count += 1
                if tick.params_updated:
                    print(
                        f"{symbol} params updated (calibration {calibrator.calibration_count()}): "
                        f"{quote.params.describe()} -> model half-spread {quote.bps(quote.model_half_spread):.2f}bp"
                    )
                if quote_count % max(args.print_every, 1) == 0 and quote_lines.ready():
                    print(
                        f"{symbol} mid={fmt_px(quote.mid)} paper=[{fmt_px(quote.bid)}, {fmt_px(quote.ask)}] "
                        f"{quote.describe()}"
                    )
                if (
                    QL_MONITOR_EVERY_N_QUOTES > 0
                    and quote_count % QL_MONITOR_EVERY_N_QUOTES == 0
                    and diagnostic_lines.ready()
                ):
                    q_annual = (
                        funding_annual(funding_rate, interval_hours) if args.market == "futures" else 0.0
                    )
                    no_jump_mean = calibrator.no_jump_conditional_mean(quote.mid, q_annual, t_years, 0.0)
                    vs_mid_bps = ((no_jump_mean - quote.mid) / quote.mid) * 10000
                    line = (
                        f"{symbol} diagnostic, not used by quotes: no-jump mean over {interval_hours:g}h="
                        f"{fmt_px(no_jump_mean)} ({vs_mid_bps:+.1f} bp vs mid)"
                    )
                    try:
                        ql_mean = calibrator.no_jump_conditional_mean_quantlib(quote.mid, q_annual, t_years, 0.0)
                        gap_bps = ((ql_mean - no_jump_mean) / quote.mid) * 10000
                        line += f"; quantlib={fmt_px(ql_mean)} (horizon rounded to whole days) gap={gap_bps:.2f}bp"
                    except Exception as exc:
                        print(f"QuantLib monitor failed: {exc}", file=sys.stderr)
                    print(line)
        except websockets.ConnectionClosed:
            print("websocket closed; reconnecting", file=sys.stderr)
            continue


def main() -> None:
    # Without a TTY (docker run without -t, or a pipe) stdout is block-buffered, which holds back the quote lines.
    sys.stdout.reconfigure(line_buffering=True)
    args = parse_args()
    if args.list_perps:
        print("\n".join(DEMO_PERPS))
        return
    try:
        require_demo_perp(args.symbol)
        policy = QuotePolicy.from_env()
        asyncio.run(run(args, policy))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
