"""Control-plane routes for bots: start / stop / status.

Bots are namespaced per user: the public ``bot_id`` a client sends (e.g. "bot-1")
is combined with the authenticated user into an internal id ``<user_id>:<bot_id>``
used for the supervisor handle, the DB row PK, and event routing. This lets two
users each have their own "bot-1" without colliding on the global supervisor /
bot_configs key. Responses echo the plain ``bot_id`` back.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..runtime.supervisor import BotState, Supervisor
from .auth import InternalAuthenticator, InternalAuthError, InternalIdentity
from .launcher import BotLauncher
from .schemas import BotStateResponse, KillAllResponse, StartBotRequest

router = APIRouter(prefix="/bots", tags=["bots"])


def _scoped(user_id: str, bot_id: str) -> str:
    """Internal, per-user bot id. Plain bot_id stays user-facing."""
    return f"{user_id}:{bot_id}"


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
    sid = _scoped(ident.user_id, bot_id)

    try:
        plan = await launcher.launch(bot_id=sid, user_id=ident.user_id, request=body)
    except ValueError as e:
        # Launcher rejected the request: live gated off, missing/invalid
        # credentials, unknown exchange, etc. — a client error, not a 500.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        await supervisor.start(
            bot_id=sid,
            user_id=ident.user_id,
            strategy=plan.strategy,
            bars=plan.bars,
            router=plan.router,
            max_loss_quote=body.risk.max_loss_quote,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    state = supervisor.get_state(sid) or BotState.STARTING
    return BotStateResponse(bot_id=bot_id, state=state.value)


@router.post("/kill-all", response_model=KillAllResponse)
async def kill_all_bots(
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> KillAllResponse:
    """Global kill switch — force-stop all of the caller's running bots."""
    supervisor: Supervisor = request.app.state.supervisor
    killed = await supervisor.kill_all(user_id=ident.user_id)
    # Strip the per-user prefix so callers see the plain ids they started with.
    prefix = f"{ident.user_id}:"
    return KillAllResponse(killed=[k.removeprefix(prefix) for k in killed])


@router.post("/{bot_id}/kill", response_model=BotStateResponse)
async def kill_bot(
    bot_id: str,
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> BotStateResponse:
    supervisor: Supervisor = request.app.state.supervisor
    sid = _scoped(ident.user_id, bot_id)
    handle = supervisor._handles.get(sid)  # type: ignore[attr-defined]
    # Unknown (or, defensively, foreign) bot → 404 to avoid leaking ids.
    if handle is None or handle.user_id != ident.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    await supervisor.kill(sid)
    final = supervisor.get_state(sid) or BotState.KILLED
    return BotStateResponse(bot_id=bot_id, state=final.value)


@router.post("/{bot_id}/stop", response_model=BotStateResponse)
async def stop_bot(
    bot_id: str,
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> BotStateResponse:
    supervisor: Supervisor = request.app.state.supervisor
    sid = _scoped(ident.user_id, bot_id)
    handle = supervisor._handles.get(sid)  # type: ignore[attr-defined]
    if handle is None or handle.user_id != ident.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    await supervisor.stop(sid)
    final = supervisor.get_state(sid) or BotState.STOPPED
    return BotStateResponse(bot_id=bot_id, state=final.value)


@router.get("/{bot_id}", response_model=BotStateResponse)
async def get_bot(
    bot_id: str,
    request: Request,
    ident: InternalIdentity = Depends(internal_identity),
) -> BotStateResponse:
    supervisor: Supervisor = request.app.state.supervisor
    sid = _scoped(ident.user_id, bot_id)
    handle = supervisor._handles.get(sid)  # type: ignore[attr-defined]
    if handle is None or handle.user_id != ident.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    return BotStateResponse(bot_id=bot_id, state=handle.state.value, last_error=handle.last_error)
