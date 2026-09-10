import sys
from pathlib import Path

import pytest

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.history import load_price_ticks, resample_last_price
from scripts.merton_runtime import paper_quotes


def test_csv_history_and_resample(tmp_path):
    csv_path = tmp_path / "ticks.csv"
    csv_path.write_text(
        "time,price\n"
        "1700000000,100.0\n"
        "1700000060,101.0\n"
        "1700003600,102.5\n"
    )
    ticks = load_price_ticks(str(csv_path))
    assert len(ticks) == 3
    assert ticks[0][1] == pytest.approx(100.0)
    bars = resample_last_price(ticks, bar_minutes=5)
    assert len(bars) == 2
    assert bars[-1][1] == pytest.approx(102.5)


def test_paper_quotes_widen_to_min_spread():
    bid, ask = paper_quotes(100.0, 99.99, 100.01, min_half_spread_bps=2.0)
    assert bid == pytest.approx(99.98)
    assert ask == pytest.approx(100.02)
