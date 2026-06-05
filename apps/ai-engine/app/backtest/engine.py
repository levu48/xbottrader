"""Backtest by replaying the live strategy classes over historical bars.

Parity by construction: the same ``xbt_core`` Strategy + ``PaperExchangeAdapter``
the Bot Engine runs in paper mode drive the backtest. Cash/position accounting
matches the supervisor's live PnL ledger (``position*mark - cash_out``), so an
equity curve here equals what the live paper bot would have produced on the same
bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from xbt_core.exchanges.paper import PaperConfig, PaperExchangeAdapter
from xbt_core.strategies.base import Bar, StrategyState
from xbt_core.strategies.factory import build_strategy

from .data import HistoricalDataClient, candle_to_bar


@dataclass(frozen=True, slots=True)
class BacktestResult:
    symbol: str
    bars: int
    starting_cash: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    num_trades: int
    win_rate: Decimal
    equity_curve: list[tuple[int, Decimal]]


async def run_backtest(
    *,
    strategy_config: object,
    bars: list[Bar],
    starting_cash: Decimal,
    paper_config: PaperConfig | None = None,
) -> BacktestResult:
    strategy = build_strategy(strategy_config)
    adapter = PaperExchangeAdapter(config=paper_config or PaperConfig())
    state = StrategyState()

    cash = starting_cash
    position = Decimal("0")
    avg_cost = Decimal("0")  # average entry price of the open position
    num_trades = 0
    wins = 0
    sells = 0
    equity_curve: list[tuple[int, Decimal]] = []
    symbol = bars[0].symbol if bars else ""

    for bar in bars:
        adapter.update_mark(bar.symbol, bar.close)
        for intent in strategy.on_bar(bar, state):
            result = await adapter.place_order(intent)
            for fill in result.immediate_fills:
                num_trades += 1
                notional = fill.quantity * fill.price
                if fill.side == "buy":
                    new_pos = position + fill.quantity
                    avg_cost = (
                        (avg_cost * position + notional) / new_pos if new_pos > 0 else Decimal("0")
                    )
                    position = new_pos
                    cash -= notional + fill.fee
                else:
                    realized = (fill.price - avg_cost) * fill.quantity - fill.fee
                    sells += 1
                    wins += 1 if realized > 0 else 0
                    position -= fill.quantity
                    cash += notional - fill.fee
        equity_curve.append((bar.ts_ms, cash + position * bar.close))

    final_equity = equity_curve[-1][1] if equity_curve else starting_cash
    total_return_pct = (
        (final_equity / starting_cash - 1) * 100 if starting_cash > 0 else Decimal("0")
    )
    return BacktestResult(
        symbol=symbol,
        bars=len(bars),
        starting_cash=starting_cash,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        num_trades=num_trades,
        win_rate=(Decimal(wins) / Decimal(sells) * 100) if sells else Decimal("0"),
        equity_curve=equity_curve,
    )


def _max_drawdown_pct(curve: list[tuple[int, Decimal]]) -> Decimal:
    peak = None
    max_dd = Decimal("0")
    for _, equity in curve:
        if peak is None or equity > peak:
            peak = equity
        if peak and peak > 0:
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd


class Backtester:
    """Wires historical data fetching to :func:`run_backtest`."""

    def __init__(
        self,
        data_client_factory,  # Callable[[str], HistoricalDataClient]
        *,
        default_exchange: str = "kraken",
    ) -> None:
        self._make_client = data_client_factory
        self._default_exchange = default_exchange

    async def run(
        self,
        *,
        strategy_config,
        symbol: str,
        timeframe: str,
        limit: int,
        since_ms: int | None,
        starting_cash: Decimal,
        exchange: str | None = None,
    ) -> BacktestResult:
        client: HistoricalDataClient = self._make_client(exchange or self._default_exchange)
        try:
            candles = await client.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        finally:
            await client.close()
        bars = [candle_to_bar(c, symbol) for c in candles]
        return await run_backtest(
            strategy_config=strategy_config, bars=bars, starting_cash=starting_cash
        )
