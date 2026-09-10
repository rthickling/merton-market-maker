"""Load user historical prices for calibrator warmup.

Accepts a directory of .parquet / .csv files or a single file.
Required columns: time (unix µs, unix seconds, or ISO-8601) and price.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

Tick = Tuple[int, float]  # epoch_us, price
BAR_MINUTES = 5


def _parse_time(value: str) -> int:
    raw = value.strip()
    if not raw:
        raise ValueError("empty time")
    try:
        n = float(raw)
        if n > 1e14:  # already microseconds
            return int(n)
        if n > 1e11:  # milliseconds
            return int(n * 1000)
        return int(n * 1_000_000)  # seconds
    except ValueError:
        pass
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000)


def _ticks_from_rows(rows: Iterable[dict]) -> List[Tick]:
    ticks: List[Tick] = []
    for row in rows:
        keys = {k.lower(): k for k in row}
        time_key = keys.get("time") or keys.get("timestamp") or keys.get("datetime")
        price_key = keys.get("price") or keys.get("close") or keys.get("px")
        if not time_key or not price_key:
            raise ValueError("history files need time/timestamp and price/close columns")
        price = float(row[price_key])
        if price <= 0:
            continue
        ticks.append((_parse_time(str(row[time_key])), price))
    ticks.sort(key=lambda t: t[0])
    return ticks


def _load_csv(path: Path) -> List[Tick]:
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header row")
        return _ticks_from_rows(reader)


def _load_parquet(path: Path) -> List[Tick]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            f"Reading {path} requires pyarrow. Convert to CSV or pip install pyarrow."
        ) from exc
    table = pq.read_table(path)
    rows = table.to_pydict()
    if not rows:
        return []
    n = len(next(iter(rows.values())))
    keys = list(rows)
    dict_rows = [{k: rows[k][i] for k in keys} for i in range(n)]
    return _ticks_from_rows(dict_rows)


def iter_history_files(data_path: str) -> Sequence[Path]:
    root = Path(data_path).expanduser()
    if not root.exists():
        raise FileNotFoundError(data_path)
    if root.is_file():
        return [root]
    files = sorted(
        [p for p in root.iterdir() if p.suffix.lower() in {".csv", ".parquet"}],
        key=lambda p: p.name,
        reverse=True,
    )
    return files


def load_price_ticks(data_path: str, max_files: int = 30) -> List[Tick]:
    files = list(iter_history_files(data_path))[:max_files]
    if not files:
        raise FileNotFoundError(f"no .csv or .parquet files under {data_path}")
    ticks: List[Tick] = []
    for path in reversed(files):  # chronological after reverse listing
        if path.suffix.lower() == ".csv":
            ticks.extend(_load_csv(path))
        else:
            ticks.extend(_load_parquet(path))
    ticks.sort(key=lambda t: t[0])
    return ticks


def resample_last_price(ticks: Sequence[Tick], bar_minutes: int = BAR_MINUTES) -> List[Tick]:
    if not ticks:
        return []
    width = bar_minutes * 60 * 1_000_000
    bars: dict[int, Tick] = {}
    for epoch_us, price in ticks:
        bucket = epoch_us // width
        bars[bucket] = (epoch_us, price)
    return [bars[k] for k in sorted(bars)]


def warmup_calibrator(calibrator, ticks: Sequence[Tick]) -> int:
    from scripts.merton_runtime import tick_calibrator

    fed = 0
    for epoch_us, price in ticks:
        tick_calibrator(calibrator, price, epoch_us)
        fed += 1
    return fed


def default_data_path() -> str | None:
    return os.getenv("MERTON_MARKET_MAKER_DATA_PATH") or None
