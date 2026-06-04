from __future__ import annotations

import time

import pytest

from app.api.auth import (
    HEADER_SIG,
    HEADER_TS,
    HEADER_USER,
    InternalAuthenticator,
    InternalAuthError,
)


def _hdrs(*, user: str, ts: int, sig: str) -> dict[str, str]:
    return {HEADER_USER: user, HEADER_TS: str(ts), HEADER_SIG: sig}


def test_round_trip_signature() -> None:
    auth = InternalAuthenticator(b"shh-its-a-secret")
    ts = int(time.time())
    sig = auth.sign(method="POST", path="/bots/b1/start", ts=ts, user_id="u1", body=b'{"k":1}')
    ident = auth.verify(
        method="POST",
        path="/bots/b1/start",
        headers=_hdrs(user="u1", ts=ts, sig=sig),
        body=b'{"k":1}',
    )
    assert ident.user_id == "u1"


def test_wrong_secret_rejected() -> None:
    a = InternalAuthenticator(b"alpha")
    b = InternalAuthenticator(b"bravo")
    ts = int(time.time())
    sig = a.sign(method="POST", path="/x", ts=ts, user_id="u1", body=b"")
    with pytest.raises(InternalAuthError, match="bad signature"):
        b.verify(method="POST", path="/x", headers=_hdrs(user="u1", ts=ts, sig=sig), body=b"")


def test_tampered_path_rejected() -> None:
    auth = InternalAuthenticator(b"s")
    ts = int(time.time())
    sig = auth.sign(method="POST", path="/bots/b1/start", ts=ts, user_id="u1", body=b"")
    with pytest.raises(InternalAuthError):
        auth.verify(
            method="POST",
            path="/bots/b2/start",
            headers=_hdrs(user="u1", ts=ts, sig=sig),
            body=b"",
        )


def test_tampered_body_rejected() -> None:
    auth = InternalAuthenticator(b"s")
    ts = int(time.time())
    sig = auth.sign(method="POST", path="/x", ts=ts, user_id="u1", body=b"original")
    with pytest.raises(InternalAuthError):
        auth.verify(
            method="POST", path="/x", headers=_hdrs(user="u1", ts=ts, sig=sig), body=b"different"
        )


def test_tampered_user_rejected() -> None:
    auth = InternalAuthenticator(b"s")
    ts = int(time.time())
    sig = auth.sign(method="POST", path="/x", ts=ts, user_id="u1", body=b"")
    with pytest.raises(InternalAuthError):
        auth.verify(
            method="POST", path="/x", headers=_hdrs(user="u2", ts=ts, sig=sig), body=b""
        )


def test_outside_skew_rejected() -> None:
    auth = InternalAuthenticator(b"s", max_skew_seconds=60)
    ts = 1_000_000
    sig = auth.sign(method="GET", path="/x", ts=ts, user_id="u1", body=b"")
    with pytest.raises(InternalAuthError, match="skew"):
        auth.verify(
            method="GET",
            path="/x",
            headers=_hdrs(user="u1", ts=ts, sig=sig),
            body=b"",
            now=ts + 120,
        )


def test_missing_headers_rejected() -> None:
    auth = InternalAuthenticator(b"s")
    with pytest.raises(InternalAuthError, match="missing"):
        auth.verify(method="GET", path="/x", headers={}, body=b"")


def test_bad_timestamp_format() -> None:
    auth = InternalAuthenticator(b"s")
    with pytest.raises(InternalAuthError, match="timestamp"):
        auth.verify(
            method="GET",
            path="/x",
            headers={HEADER_USER: "u1", HEADER_TS: "not-a-number", HEADER_SIG: "deadbeef"},
            body=b"",
        )


def test_empty_secret_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        InternalAuthenticator(b"")
