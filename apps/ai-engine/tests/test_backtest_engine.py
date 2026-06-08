from __future__ import annotations

from decimal import Decimal

import pytest
from xbt_core.exchanges.paper import PaperConfig
from xbt_core.strategies.base import Bar
from xbt_core.strategy_config import AiSignalParams

from app.api.schemas import DcaParams, MaCrossoverParams
from app.backtest.engine import run_backtest


def _bar(ts_ms: int, close: str, sym: str = "BTC/USDT") -> Bar:
    p = Decimal(close)
    return Bar(ts_ms=ts_ms, symbol=sym, open=p, high=p, low=p, close=p, volume=Decimal("0"))


async def test_dca_zero_cost_flat_price_preserves_equity() -> None:
    # No slippage/fees + flat price → buying changes nothing: equity stays flat.
    cfg = DcaParams(
        strategy_type="dca", symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=1
    )
    bars = [_bar(i * 60_000, "100") for i in range(3)]
    res = await run_backtest(
        strategy_config=cfg,
        bars=bars,
        starting_cash=Decimal("1000"),
        paper_config=PaperConfig(slippage_bps=0, fee_bps=0),
    )
    assert res.num_trades == 3  # buys on each interval
    assert res.bars == 3
    assert res.final_equity == Decimal("1000")
    assert res.total_return_pct == Decimal("0")
    assert res.max_drawdown_pct == Decimal("0")


async def test_ai_signal_is_rejected_not_backtestable() -> None:
    # ai_signal decisions come from a live LLM; a backtest can't replay them, so
    # run_backtest must reject rather than emit a misleading flat curve.
    cfg = AiSignalParams(
        strategy_type="ai_signal",
        symbol="BTC/USDT",
        quote_amount=Decimal("100"),
        decision_interval_minutes=60,
    )
    bars = [_bar(i * 60_000, "100") for i in range(3)]
    with pytest.raises(ValueError, match="not backtestable"):
        await run_backtest(strategy_config=cfg, bars=bars, starting_cash=Decimal("1000"))


async def test_dca_fees_drag_equity_below_start() -> None:
    cfg = DcaParams(
        strategy_type="dca", symbol="BTC/USDT", quote_amount=Decimal("100"), interval_minutes=1
    )
    bars = [_bar(i * 60_000, "100") for i in range(2)]
    res = await run_backtest(strategy_config=cfg, bars=bars, starting_cash=Decimal("1000"))
    assert res.final_equity < Decimal("1000")  # slippage + fee drag
    assert res.total_return_pct < Decimal("0")


async def test_ma_crossover_round_trip_counts_trade_and_win_rate() -> None:
    cfg = MaCrossoverParams(
        strategy_type="ma_crossover",
        symbol="BTC/USDT",
        fast_period=2,
        slow_period=3,
        position_quote=Decimal("100"),
    )
    # golden cross at bar 3 (close 13), death cross at bar 4 (close 5)
    closes = ["10", "10", "10", "13", "5"]
    bars = [_bar(i * 60_000, c) for i, c in enumerate(closes)]
    res = await run_backtest(strategy_config=cfg, bars=bars, starting_cash=Decimal("1000"))

    assert res.num_trades == 2  # one buy + one sell
    assert res.win_rate == Decimal("0")  # sold lower than bought → losing round-trip
    assert len(res.equity_curve) == 5
