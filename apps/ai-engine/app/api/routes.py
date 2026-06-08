"""AI Engine control-plane routes: copilot chat + backtest. HMAC-authenticated."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from xbt_core.internal_auth import InternalAuthenticator, InternalAuthError, InternalIdentity

from ..backtest.engine import Backtester
from ..llm.author import StrategyAuthor, StrategyAuthorError
from ..llm.copilot import Copilot
from ..llm.signal import SignalAdvisor
from .schemas import (
    AuthorStrategyRequest,
    AuthorStrategyResponse,
    BacktestRequest,
    BacktestResponse,
    BacktestStats,
    CopilotChatRequest,
    CopilotChatResponse,
    EquityPoint,
    SignalRequest,
    SignalResponse,
)

router = APIRouter(tags=["ai"])


async def internal_identity(request: Request) -> InternalIdentity:
    auth: InternalAuthenticator = request.app.state.internal_auth
    body = await request.body()
    try:
        return auth.verify(
            method=request.method,
            path=request.url.path,
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
        )
    except InternalAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e


@router.post("/copilot/chat", response_model=CopilotChatResponse)
async def copilot_chat(
    body: CopilotChatRequest,
    request: Request,
    _ident: InternalIdentity = Depends(internal_identity),
) -> CopilotChatResponse:
    copilot: Copilot = request.app.state.copilot
    result = copilot.chat(
        [m.model_dump() for m in body.messages], context=body.context
    )
    return CopilotChatResponse(reply=result.reply, usage=result.usage)


@router.post("/strategy/author", response_model=AuthorStrategyResponse)
async def strategy_author(
    body: AuthorStrategyRequest,
    request: Request,
    _ident: InternalIdentity = Depends(internal_identity),
) -> AuthorStrategyResponse:
    author: StrategyAuthor = request.app.state.author
    try:
        result = author.author(description=body.description, symbol=body.symbol)
    except StrategyAuthorError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return AuthorStrategyResponse(
        strategy=result.strategy, explanation=result.explanation, usage=result.usage
    )


@router.post("/strategy/signal", response_model=SignalResponse)
async def strategy_signal(
    body: SignalRequest,
    request: Request,
    _ident: InternalIdentity = Depends(internal_identity),
) -> SignalResponse:
    advisor: SignalAdvisor = request.app.state.signal_advisor
    result = advisor.decide(
        symbol=body.symbol,
        closes=body.closes,
        position=body.position,
        guidance=body.guidance,
        model=body.model,
    )
    return SignalResponse(action=result.action, reason=result.reason, usage=result.usage)


@router.post("/backtest/run", response_model=BacktestResponse)
async def backtest_run(
    body: BacktestRequest,
    request: Request,
    _ident: InternalIdentity = Depends(internal_identity),
) -> BacktestResponse:
    backtester: Backtester = request.app.state.backtester
    try:
        result = await backtester.run(
            strategy_config=body.strategy,
            symbol=body.strategy.symbol,
            timeframe=body.timeframe,
            limit=body.limit,
            since_ms=body.since_ms,
            starting_cash=body.starting_cash,
            exchange=body.exchange,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return BacktestResponse(
        symbol=result.symbol,
        timeframe=body.timeframe,
        bars=result.bars,
        stats=BacktestStats(
            starting_cash=result.starting_cash,
            final_equity=result.final_equity,
            total_return_pct=result.total_return_pct,
            max_drawdown_pct=result.max_drawdown_pct,
            num_trades=result.num_trades,
            win_rate=result.win_rate,
        ),
        equity_curve=[EquityPoint(ts_ms=ts, equity=eq) for ts, eq in result.equity_curve],
    )
