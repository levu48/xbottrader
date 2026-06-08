"""Trading-session calendar — when is a venue's market open?

A :class:`MarketSession` answers ``is_open(ts_ms)`` for a UTC-epoch-millisecond
instant. Crypto trades 24/7 (:class:`CryptoSession` always open); US equities
follow NYSE regular hours 09:30–16:00 America/New_York, Mon–Fri, minus holidays
(:class:`UsEquitySession`).

Design notes:
- **Pure and offline.** No network, no per-call I/O — same philosophy as the
  Strategy/paper-fill domain, so it's deterministic in tests and safe to call on
  the hot path (e.g. the supervisor's circuit breaker on every bar).
- **Static holiday set.** NYSE full-closure holidays are hardcoded for the
  current + next year (see ``_NYSE_HOLIDAYS``). REFRESH ANNUALLY — there's a test
  (``test_market_session``) that fails if the set doesn't cover the current year.
  Early-close (half) days are treated as full sessions for the MVP; modelling the
  13:00 ET early close is a deferred refinement.
- ``session_for(venue)`` is the single resolver the launcher uses to pick a
  session for a bot. Venue uniquely determines asset class here, so we derive it
  rather than carrying a separate request field.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Protocol
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)

# Venues that settle as US equities. Everything else is treated as crypto (24/7).
_EQUITY_VENUES = frozenset({"alpaca", "alpaca-paper"})

# NYSE full-closure holidays (observed dates). Weekend-adjusted per NYSE rules.
# Covers 2026–2027; REFRESH ANNUALLY (test_market_session asserts current-year
# coverage). dates are in America/New_York calendar terms.
_NYSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2026
        date(2026, 1, 1),   # New Year's Day
        date(2026, 1, 19),  # MLK Jr. Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),   # Independence Day (observed, Jul 4 = Sat)
        date(2026, 9, 7),   # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
        # 2027
        date(2027, 1, 1),   # New Year's Day
        date(2027, 1, 18),  # MLK Jr. Day
        date(2027, 2, 15),  # Washington's Birthday
        date(2027, 3, 26),  # Good Friday
        date(2027, 5, 31),  # Memorial Day
        date(2027, 6, 18),  # Juneteenth (observed, Jun 19 = Sat)
        date(2027, 7, 5),   # Independence Day (observed, Jul 4 = Sun)
        date(2027, 9, 6),   # Labor Day
        date(2027, 11, 25),  # Thanksgiving
        date(2027, 12, 24),  # Christmas (observed, Dec 25 = Sat)
    }
)


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    US_EQUITY = "us_equity"


class MarketSession(Protocol):
    """Whether a venue's market is open at a given instant (UTC epoch ms)."""

    def is_open(self, ts_ms: int) -> bool:
        ...

    def next_open_ms(self, ts_ms: int) -> int | None:
        """Next instant (UTC epoch ms) the market opens strictly after ``ts_ms``.

        ``None`` if the market is always open (no meaningful next-open).
        """
        ...


class CryptoSession:
    """24/7 — always open. Used for every crypto venue (and as the default)."""

    def is_open(self, ts_ms: int) -> bool:
        return True

    def next_open_ms(self, ts_ms: int) -> int | None:
        return None


class UsEquitySession:
    """NYSE regular hours: 09:30–16:00 ET, Mon–Fri, minus full-closure holidays."""

    def is_open(self, ts_ms: int) -> bool:
        et = _to_et(ts_ms)
        if not _is_trading_day(et.date()):
            return False
        return _OPEN <= et.timetz().replace(tzinfo=None) < _CLOSE

    def next_open_ms(self, ts_ms: int) -> int | None:
        et = _to_et(ts_ms)
        # If before today's open on a trading day, the next open is today.
        day = et.date()
        for _ in range(0, 366):  # bounded scan; always resolves well within a year
            if _is_trading_day(day):
                open_dt = datetime.combine(day, _OPEN, tzinfo=_ET)
                if open_dt.timestamp() * 1000 > ts_ms:
                    return int(open_dt.timestamp() * 1000)
            day = day + timedelta(days=1)
        return None


def _to_et(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(_ET)


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _NYSE_HOLIDAYS


def asset_class_for(venue: str) -> AssetClass:
    return AssetClass.US_EQUITY if venue in _EQUITY_VENUES else AssetClass.CRYPTO


def session_for(venue: str) -> MarketSession:
    """Resolve a venue id to its market session. Crypto venues are always-open."""
    if asset_class_for(venue) is AssetClass.US_EQUITY:
        return UsEquitySession()
    return CryptoSession()
