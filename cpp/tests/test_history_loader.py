import inspect
import re
import sys
from pathlib import Path

import pytest

_REPO = Path("/repo") if Path("/repo/scripts").is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.binance_perps import DEMO_PERPS, require_demo_perp
from scripts.history import load_price_ticks, resample_last_price
from scripts.merton_runtime import funding_annual, horizon_years, midpoint_quotes, paper_quotes


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


def test_paper_quotes_accepts_its_former_keyword():
    expected = paper_quotes(100.0, 99.99, 100.01, 2.0)
    assert paper_quotes(theo=100.0, mkt_bid=99.99, mkt_ask=100.01, min_half_spread_bps=2.0) == expected
    assert paper_quotes(reference_price=100.0, mkt_bid=99.99, mkt_ask=100.01, min_half_spread_bps=2.0) == expected
    assert list(inspect.signature(paper_quotes).parameters)[0] == "reference_price"
    with pytest.raises(TypeError):
        paper_quotes(theo=100.0, reference_price=100.0, mkt_bid=99.99, mkt_ask=100.01)
    with pytest.raises(TypeError):
        paper_quotes(99.99, 100.01, theo=100.0)


@pytest.mark.parametrize(
    "bid,ask,half_spread",
    [
        (99.0, 101.0, 1.0),  # wide book: half the market spread
        (99.999, 100.001, 0.02),  # tight book: the 2 bp floor
        (100.0, 100.0, 0.02),  # locked book: the floor
        (68_000.0, 68_000.1, 68_000.05 * 2e-4),  # the floor scales with mid
    ],
)
def test_midpoint_quotes_are_centred_on_mid_and_keep_the_floor(bid, ask, half_spread):
    mid, quote_bid, quote_ask = midpoint_quotes(bid, ask, min_half_spread_bps=2.0)
    assert mid == (bid + ask) / 2.0
    assert (quote_bid + quote_ask) / 2.0 == pytest.approx(mid, rel=1e-15)
    assert quote_ask - mid == pytest.approx(half_spread, rel=1e-9)
    assert mid - quote_bid == pytest.approx(half_spread, rel=1e-9)
    assert quote_bid <= bid and quote_ask >= ask


def test_replay_quote_is_centred_on_the_last_price_and_explains_its_width(capsys):
    from scripts import replay_demo

    replay_demo.run(replay_demo.parse_args(["--synthetic"]))
    out = capsys.readouterr().out
    quote = re.search(
        r"^quote: mid=([\d.]+) paper=\[([\d.]+), ([\d.]+)\] half=([\d.]+)bp set by (model|floor|market spread) "
        r"\(model ([\d.]+)bp, floor ([\d.]+)bp, market ([\d.]+)bp; 60s horizon; params (seeded|calibrated)\)$",
        out,
        re.MULTILINE,
    )
    assert quote, out
    mid, quote_bid, quote_ask, half, model, floor, market = map(float, quote.group(1, 2, 3, 4, 6, 7, 8))
    assert (quote_bid + quote_ask) / 2.0 == pytest.approx(mid, abs=0.01)
    assert (quote_ask - quote_bid) / 2.0 / mid * 10_000 == pytest.approx(half, abs=0.01)
    assert half == max(model, floor, market)
    assert (floor, market) == (2.0, 0.0)
    assert quote.group(5) == "model" and quote.group(9) == "calibrated"
    assert re.search(r"^initial: .* \(seeded; model half-spread [\d.]+bp\)$", out, re.MULTILINE), out
    assert re.search(r"^tick +\d+  mid=[\d.]+  sigma=.* model=[\d.]+bp$", out, re.MULTILINE), out
    assert re.search(r"^diagnostic: no-jump mean over 8h=[\d.]+ \([+-]\d+\.\d bp vs mid\)$", out, re.MULTILINE), out


def test_replay_policy_flags_change_the_width_and_are_validated(capsys):
    from scripts import replay_demo

    def half_spread_bps(*flags):
        replay_demo.run(replay_demo.parse_args(["--synthetic", *flags]))
        return float(re.search(r"^quote: .* half=([\d.]+)bp", capsys.readouterr().out, re.MULTILINE).group(1))

    base = half_spread_bps()
    assert half_spread_bps("--risk-horizon-seconds", "240") == pytest.approx(2 * base, abs=0.02)
    assert half_spread_bps("--risk-multiplier", "0.1", "--min-half-spread-bps", "5") == 5.0
    with pytest.raises(SystemExit, match="risk_multiplier"):
        replay_demo.run(replay_demo.parse_args(["--synthetic", "--risk-multiplier", "0"]))


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
