#!/usr/bin/env python3
"""Replay ticks through the C++ calibrator offline and print a short summary.

Default input is a seeded synthetic Merton path. Pass --data-path or set
MERTON_MARKET_MAKER_DATA_PATH to replay recorded CSV/Parquet instead.
Floats are printed with fixed formats so unchanged input yields byte-identical
output. Intended to be started from the repository root, e.g. `cd cpp && just replay`.

The closing paper quote is centred on the last price, the replay's midpoint;
with no book to replay, the minimum half-spread sets its width. The no-jump
conditional mean is printed as a labelled diagnostic, not as a quote input.
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
    build_calibrator,
    midpoint_quotes,
)
from scripts.synthetic_ticks import PathSpec, merton_ticks

# Fixed seeds so host .env cannot change the default synthetic run.
SEEDS = {"sigma": 0.44, "lambda": 20.0, "mu_j": 0.003, "delta_j": 0.01}
HALF_SPREAD_BPS = 2.0
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


def fmt_params(p) -> str:
    return (
        f"sigma={p.sigma:.4f} lambda={getattr(p, 'lambda'):.3f} "
        f"mu_j={p.mu_j:.6f} delta_j={p.delta_j:.6f}"
    )


def select_events(events: list, limit: int = MAX_EVENT_LINES) -> list:
    if len(events) <= limit:
        return events
    head = limit // 2
    tail = limit - head
    return events[:head] + events[-tail:]


def run(args: argparse.Namespace) -> None:
    ticks, source = load_ticks(args)
    moc = _import_moc()
    cal = build_calibrator(moc, seeds=SEEDS)

    events: list[tuple[int, float, object]] = []
    for i, (price, epoch_us) in enumerate(ticks, start=1):
        if not cal.update_tick(price, epoch_us):
            continue
        if cal.maybe_update_params():
            events.append((i, price, cal.params()))

    print("Offline Merton calibrator replay")
    print(
        f"source: {source}; window={CPP_WINDOW_SIZE}, "
        f"update_every={CPP_UPDATE_EVERY_N_RETURNS}, n_max={CPP_N_MAX}"
    )
    print(
        f"initial: sigma={SEEDS['sigma']:.4f} lambda={SEEDS['lambda']:.3f} "
        f"mu_j={SEEDS['mu_j']:.6f} delta_j={SEEDS['delta_j']:.6f}"
    )
    print()

    chosen = select_events(events)
    omitted = len(events) - len(chosen)
    for idx, (tick_i, price, params) in enumerate(chosen):
        if omitted and idx == len(chosen) // 2:
            print(f"... ({omitted} more parameter changes)")
        print(f"tick {tick_i:>6}  mid={fmt_px(price)}  {fmt_params(params)}")

    last_price = ticks[-1][0]
    mid, quote_bid, quote_ask = midpoint_quotes(last_price, last_price, min_half_spread_bps=HALF_SPREAD_BPS)
    no_jump_mean = cal.no_jump_conditional_mean(mid, 0.0, T_YEARS, 0.0)
    vs_mid_bps = ((no_jump_mean - mid) / mid) * 10000.0 if mid else 0.0
    print()
    print(
        f"final: ticks={len(ticks)} samples={cal.sample_count()} "
        f"calibrations={cal.calibration_count()} changed={len(events)}"
    )
    print(f"params: {fmt_params(cal.params())}")
    print(f"quote: mid={fmt_px(mid)} paper=[{fmt_px(quote_bid)}, {fmt_px(quote_ask)}]")
    print(f"diagnostic: no-jump mean over {T_HOURS:g}h={fmt_px(no_jump_mean)} ({vs_mid_bps:+.1f} bp vs mid)")


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
