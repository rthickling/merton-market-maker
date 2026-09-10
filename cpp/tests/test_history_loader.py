import sys
from pathlib import Path

import pytest

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.binance_perps import DEMO_PERPS, require_demo_perp
from scripts.history import load_price_ticks, resample_last_price
from scripts.merton_runtime import funding_annual, horizon_years, paper_quotes


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


def test_funding_annual_scales_with_interval():
    rate = 0.0001
    a8 = funding_annual(rate, 8)
    a4 = funding_annual(rate, 4)
    a1 = funding_annual(rate, 1)
    assert a4 == pytest.approx(2 * a8)
    assert a1 == pytest.approx(8 * a8)
    assert horizon_years(4) == pytest.approx(0.5 * horizon_years(8))


def test_demo_perps_are_five_usdt_symbols():
    assert len(DEMO_PERPS) == 5
    assert DEMO_PERPS[0] == "BTCUSDT"
    assert "XAUUSDT" in DEMO_PERPS
    assert require_demo_perp("ethusdt") == "ETHUSDT"
    with pytest.raises(ValueError):
        require_demo_perp("DOGEUSDT")
