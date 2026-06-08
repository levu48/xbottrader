"""Async client for the AI Engine's internal HTTP API.

The Bot Engine's ai_signal companion calls ``POST /strategy/signal`` to turn a
window of recent closes into a buy/sell/hold decision. Auth is the same HMAC
scheme the Gateway uses (see :mod:`xbt_core.internal_auth`); the Bot Engine signs
with the shared ``GATEWAY_INTERNAL_HMAC_SECRET`` that the AI Engine verifies.

The signature covers the exact request bytes, so we serialize the body once and
send those bytes verbatim (``content=``) rather than letting httpx re-encode.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx
from xbt_core.internal_auth import (
    HEADER_SIG,
    HEADER_TS,
    HEADER_USER,
    InternalAuthenticator,
)

_SIGNAL_PATH = "/strategy/signal"
_DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class SignalDecision:
    action: str  # "buy" | "sell" | "hold"
    reason: str


class AiEngineError(Exception):
    """A non-2xx response (or transport failure) from the AI Engine."""


class AiEngineClient:
    def __init__(
        self,
        base_url: str,
        authenticator: InternalAuthenticator,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not base_url:
            raise ValueError("AI Engine base_url must be non-empty")
        self._base_url = base_url.rstrip("/")
        self._auth = authenticator
        # Own a long-lived client by default; tests inject one over a MockTransport.
        self._http = http or httpx.AsyncClient(timeout=timeout_s)

    async def signal(
        self,
        *,
        user_id: str,
        symbol: str,
        closes: list[Decimal],
        position: Decimal,
        guidance: str | None = None,
        model: str | None = None,
    ) -> SignalDecision:
        # Decimals aren't JSON-native — send them as strings; pydantic on the AI
        # Engine coerces them straight back to Decimal.
        payload: dict[str, object] = {
            "symbol": symbol,
            "closes": [str(c) for c in closes],
            "position": str(position),
        }
        if guidance is not None:
            payload["guidance"] = guidance
        if model is not None:
            payload["model"] = model
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        ts = int(time.time())
        sig = self._auth.sign(
            method="POST", path=_SIGNAL_PATH, ts=ts, user_id=user_id, body=body
        )
        headers = {
            HEADER_USER: user_id,
            HEADER_TS: str(ts),
            HEADER_SIG: sig,
            "content-type": "application/json",
        }
        try:
            resp = await self._http.post(
                f"{self._base_url}{_SIGNAL_PATH}", content=body, headers=headers
            )
        except httpx.HTTPError as e:
            raise AiEngineError(f"ai engine request failed: {e}") from e
        if resp.status_code >= 400:
            raise AiEngineError(f"ai engine {resp.status_code}: {resp.text}")

        data = resp.json()
        action = data.get("action")
        if action not in ("buy", "sell", "hold"):
            raise AiEngineError(f"ai engine returned invalid action: {action!r}")
        return SignalDecision(action=action, reason=str(data.get("reason", "")))

    async def aclose(self) -> None:
        await self._http.aclose()
