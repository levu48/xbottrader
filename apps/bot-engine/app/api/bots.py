"""Control-plane routes for bots: start / stop / status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..runtime.supervisor import BotState, Supervisor
from .auth import InternalAuthenticator, InternalAuthError, InternalIdentity
from .launcher import BotLauncher
from .schemas import BotStateResponse, KillAllResponse, StartBotRequest

router = APIRouter(prefix="/bots", tags=["bots"])


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


@router.post("/{bot_id}/start", response_model=BotStateResponse)
async def start_bot(
    bot_id: str,
    body: StartBotRequest,
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> BotStateResponse:
    supervisor: Supervisor = request.app.state.supervisor
    launcher: BotLauncher = request.app.state.launcher

    plan = await launcher.launch(bot_id=bot_id, user_id=ident.user_id, request=body)
    try:
        await supervisor.start(
            bot_id=bot_id,
            user_id=ident.user_id,
            strategy=plan.strategy,
            bars=plan.bars,
            router=plan.router,
            max_loss_quote=body.risk.max_loss_quote,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    state = supervisor.get_state(bot_id) or BotState.STARTING
    return BotStateResponse(bot_id=bot_id, state=state.value)


@router.post("/kill-all", response_model=KillAllResponse)
async def kill_all_bots(
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> KillAllResponse:
    """Global kill switch — force-stop all of the caller's running bots."""
    supervisor: Supervisor = request.app.state.supervisor
    killed = await supervisor.kill_all(user_id=ident.user_id)
    return KillAllResponse(killed=killed)


@router.post("/{bot_id}/kill", response_model=BotStateResponse)
async def kill_bot(
    bot_id: str,
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> BotStateResponse:
    supervisor: Supervisor = request.app.state.supervisor
    handle = supervisor._handles.get(bot_id)  # type: ignore[attr-defined]
    # Same ownership rule as GET: a user can only see/act on their own bots, and
    # an unknown or foreign bot is indistinguishable (404) to avoid leaking ids.
    if handle is None or handle.user_id != ident.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    await supervisor.kill(bot_id)
    final = supervisor.get_state(bot_id) or BotState.KILLED
    return BotStateResponse(bot_id=bot_id, state=final.value)


@router.post("/{bot_id}/stop", response_model=BotStateResponse)
async def stop_bot(
    bot_id: str,
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> BotStateResponse:
    supervisor: Supervisor = request.app.state.supervisor
    state = supervisor.get_state(bot_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    await supervisor.stop(bot_id)
    final = supervisor.get_state(bot_id) or BotState.STOPPED
    return BotStateResponse(bot_id=bot_id, state=final.value)


@router.get("/{bot_id}", response_model=BotStateResponse)
async def get_bot(
    bot_id: str,
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> BotStateResponse:
    supervisor: Supervisor = request.app.state.supervisor
    handle = supervisor._handles.get(bot_id)  # type: ignore[attr-defined]
    if handle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    if handle.user_id != ident.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    return BotStateResponse(
        bot_id=bot_id, state=handle.state.value, last_error=handle.last_error
    )
