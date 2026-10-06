#!/usr/bin/env python3
"""Replay ticks through the C++ calibrator offline and print a short summary.

Default input is a seeded synthetic Merton path. Pass --data-path or set
MERTON_MARKET_MAKER_DATA_PATH to replay recorded CSV/Parquet instead.
Floats are printed with fixed formats so unchanged input yields byte-identical
output. Intended to be started from the repository root, e.g. `cd cpp && just replay`.

Every tick is quoted the way the live demo quotes a book update
(scripts/merton_runtime.py quote_tick), with the price as the midpoint and a
zero market spread, so the width comes from the floor or from the model. The
quote policy is set by flags, not the environment, to keep the output
reproducible. The no-jump conditional mean is printed as a labelled diagnostic,
not as a quote input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.history import default_data_path, load_price_ticks, resample_last_price
from scripts.merton_runtime import (
    CPP_N_MAX,
    CPP_UPDATE_EVERY_N_RETURNS,
    CPP_WINDOW_SIZE,
    T_HOURS,
    T_YEARS,
    QuoteError,
    QuotePolicy,
    build_calibrator,
    quote_tick,
)
from scripts.synthetic_ticks import PathSpec, merton_ticks

# Fixed seeds so host .env cannot change the default synthetic run.
SEEDS = {"sigma": 0.44, "lambda": 20.0, "mu_j": 0.003, "delta_j": 0.01}
DEFAULT_POLICY = QuotePolicy()
MAX_EVENT_LINES = 4


def _import_moc():
    try:
        import merton_online_calibrator as moc
    except ImportError as exc:
        raise SystemExit(
            "Could not import merton_online_calibrator. "
            "Build with `cd cpp && just build` and keep PYTHONPATH on the build directory."
        ) from exc
    return moc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--data-path",
        default=default_data_path(),
        help="CSV/Parquet ticks (time + price). Default: MERTON_MARKET_MAKER_DATA_PATH, else synthetic.",
    )
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="Use the seeded synthetic path even if a data path is set.",
    )
    p.add_argument("--seed", type=int, default=7, help="Synthetic path seed (default 7)")
    p.add_argument(
        "--ticks",
        type=int,
        default=CPP_WINDOW_SIZE + 1,
        help=f"Synthetic tick count (default {CPP_WINDOW_SIZE + 1})",
    )
    p.add_argument(
        "--risk-horizon-seconds",
        type=float,
        default=DEFAULT_POLICY.risk_horizon_seconds,
        help=f"Horizon of the model width (default {DEFAULT_POLICY.risk_horizon_seconds:g})",
    )
    p.add_argument(
        "--risk-multiplier",
        type=float,
        default=DEFAULT_POLICY.risk_multiplier,
        help=f"Multiplier on the model width (default {DEFAULT_POLICY.risk_multiplier:g})",
    )
    p.add_argument(
        "--min-half-spread-bps",
        type=float,
        default=DEFAULT_POLICY.min_half_spread_bps,
        help=f"Half-spread floor in bp of mid (default {DEFAULT_POLICY.min_half_spread_bps:g})",
    )
    return p.parse_args(argv)


def load_ticks(args: argparse.Namespace) -> tuple[list[tuple[float, int]], str]:
    """Return (price, epoch_us) pairs and a one-line source description."""
    if not args.synthetic and args.data_path:
        path = Path(args.data_path).expanduser()
        if not path.exists():
            raise SystemExit(f"data path not found: {path}")
        history = resample_last_price(load_price_ticks(str(path)))
        if not history:
            raise SystemExit(f"no ticks loaded from {path}")
        ticks = [(price, epoch_us) for epoch_us, price in history]
        return ticks, f"recorded, {len(ticks)} bars from {path.name}"

    spec = PathSpec(seed=args.seed, n_ticks=args.ticks)
    ticks = merton_ticks(spec)
    hours = spec.dt_seconds / 3600.0
    spacing = f"{hours:g}h" if hours >= 1 else f"{spec.dt_seconds:g}s"
    return (
        ticks,
        f"synthetic seed={spec.seed}, {len(ticks)} {spacing} ticks, s0={spec.s0:.0f}",
    )


def fmt_px(price: float) -> str:
    abs_px = abs(price)
    if abs_px >= 100:
        return f"{price:.2f}"
    if abs_px >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}"


def select_events(events: list, limit: int = MAX_EVENT_LINES) -> list:
    if len(events) <= limit:
        return events
    head = limit // 2
    tail = limit - head
    return events[:head] + events[-tail:]


def run(args: argparse.Namespace) -> None:
    try:
        policy = QuotePolicy(args.risk_horizon_seconds, args.risk_multiplier, args.min_half_spread_bps)
    except ValueError as exc:
        raise SystemExit(f"invalid quote policy: {exc}") from exc
    ticks, source = load_ticks(args)
    moc = _import_moc()
    cal = build_calibrator(moc, seeds=SEEDS)

    first = None
    events = []
    for i, (price, epoch_us) in enumerate(ticks, start=1):
        try:
            tick, quote = quote_tick(cal, price, price, epoch_us, policy)
        except QuoteError as exc:
            raise SystemExit(f"tick {i}: {exc}") from exc
        if first is None:
            first = quote
        if tick.params_updated:
            events.append((i, quote))
    last = quote

    print("Offline Merton calibrator replay")
    print(
        f"source: {source}; window={CPP_WINDOW_SIZE}, "
        f"update_every={CPP_UPDATE_EVERY_N_RETURNS}, n_max={CPP_N_MAX}"
    )
    print(
        f"policy: half-spread = max(market half-spread, {policy.min_half_spread_bps:g}bp floor, "
        f"{policy.risk_multiplier:g} x mid x model sd over {policy.risk_horizon_seconds:g}s); "
        "replayed prices have no book, so the market half-spread is 0"
    )
    print(
        f"initial: {first.params.describe()} "
        f"({first.params_source}; model half-spread {first.bps(first.model_half_spread):.2f}bp)"
    )
    print()

    chosen = select_events(events)
    omitted = len(events) - len(chosen)
    for idx, (tick_i, quote) in enumerate(chosen):
        if omitted and idx == len(chosen) // 2:
            print(f"... ({omitted} more parameter changes)")
        print(
            f"tick {tick_i:>6}  mid={fmt_px(quote.mid)}  {quote.params.describe()}  "
            f"model={quote.bps(quote.model_half_spread):.2f}bp"
        )

    no_jump_mean = cal.no_jump_conditional_mean(last.mid, 0.0, T_YEARS, 0.0)
    vs_mid_bps = ((no_jump_mean - last.mid) / last.mid) * 10000.0
    print()
    print(
        f"final: ticks={len(ticks)} samples={cal.sample_count()} "
        f"calibrations={cal.calibration_count()} changed={len(events)}"
    )
    print(f"params: {last.params.describe()}")
    print(f"quote: mid={fmt_px(last.mid)} paper=[{fmt_px(last.bid)}, {fmt_px(last.ask)}] {last.describe()}")
    print(f"diagnostic: no-jump mean over {T_HOURS:g}h={fmt_px(no_jump_mean)} ({vs_mid_bps:+.1f} bp vs mid)")


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
