"""Re-export — internal HMAC auth now lives in xbt_core (shared with AI Engine)."""

from xbt_core.internal_auth import (  # noqa: F401
    DEFAULT_SKEW_SECONDS,
    HEADER_SIG,
    HEADER_TS,
    HEADER_USER,
    InternalAuthenticator,
    InternalAuthError,
    InternalIdentity,
)
