"""HMAC verification for internal Gateway → Python service calls.

The Gateway authenticates the user (session/2FA) and then forwards the
``user_id`` to internal services with an HMAC signature. Python services trust
that signature instead of re-authenticating, keeping the auth surface in Node.
Shared by the Bot Engine and AI Engine.

Signature scheme (must match apps/gateway/src/clients/internal-auth.ts):

    msg = method + "\\n" + path + "\\n" + ts + "\\n" + user_id + "\\n" + hex(sha256(body))
    sig = hex(hmac_sha256(secret, msg))

Headers on the request:
    X-XBT-User: <user_id>
    X-XBT-Ts:   <unix seconds>
    X-XBT-Sig:  <hex sha256 hmac>
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

HEADER_USER = "x-xbt-user"
HEADER_TS = "x-xbt-ts"
HEADER_SIG = "x-xbt-sig"

DEFAULT_SKEW_SECONDS = 300  # ±5 min


@dataclass(frozen=True, slots=True)
class InternalIdentity:
    user_id: str


class InternalAuthError(Exception):
    pass


class InternalAuthenticator:
    def __init__(self, secret: bytes, *, max_skew_seconds: int = DEFAULT_SKEW_SECONDS) -> None:
        if not secret:
            raise ValueError("internal auth secret must be non-empty")
        self._secret = secret
        self._skew = max_skew_seconds

    @classmethod
    def from_env(cls, raw: str, *, max_skew_seconds: int = DEFAULT_SKEW_SECONDS) -> "InternalAuthenticator":
        return cls(raw.encode("utf-8"), max_skew_seconds=max_skew_seconds)

    def sign(self, *, method: str, path: str, ts: int, user_id: str, body: bytes) -> str:
        msg = _canonical(method=method, path=path, ts=ts, user_id=user_id, body=body)
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def verify(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        now: int | None = None,
    ) -> InternalIdentity:
        user_id = headers.get(HEADER_USER) or headers.get(HEADER_USER.title())
        ts_str = headers.get(HEADER_TS) or headers.get(HEADER_TS.title())
        sig = headers.get(HEADER_SIG) or headers.get(HEADER_SIG.title())

        if not user_id or not ts_str or not sig:
            raise InternalAuthError("missing auth headers")
        try:
            ts = int(ts_str)
        except ValueError as e:
            raise InternalAuthError("bad timestamp") from e

        current = now if now is not None else int(time.time())
        if abs(current - ts) > self._skew:
            raise InternalAuthError("timestamp outside skew window")

        expected = self.sign(method=method.upper(), path=path, ts=ts, user_id=user_id, body=body)
        if not hmac.compare_digest(expected, sig):
            raise InternalAuthError("bad signature")
        return InternalIdentity(user_id=user_id)


def _canonical(*, method: str, path: str, ts: int, user_id: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{ts}\n{user_id}\n{body_hash}".encode("utf-8")
