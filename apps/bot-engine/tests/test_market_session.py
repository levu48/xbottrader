"""Trading-session calendar — boundaries, holidays, weekends, asset-class routing."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from xbt_core.market.session import (
    AssetClass,
    CryptoSession,
    UsEquitySession,
    _NYSE_HOLIDAYS,
    asset_class_for,
    session_for,
)

_ET = ZoneInfo("America/New_York")


def _ms(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=_ET).timestamp() * 1000)


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def test_session_for_routes_by_venue() -> None:
    assert isinstance(session_for("alpaca"), UsEquitySession)
    assert isinstance(session_for("alpaca-paper"), UsEquitySession)
    assert isinstance(session_for("binance"), CryptoSession)
    assert isinstance(session_for("coinbase"), CryptoSession)


def test_asset_class_for() -> None:
    assert asset_class_for("alpaca") is AssetClass.US_EQUITY
    assert asset_class_for("alpaca-paper") is AssetClass.US_EQUITY
    assert asset_class_for("binance") is AssetClass.CRYPTO


# --------------------------------------------------------------------------- #
# Crypto — always open
# --------------------------------------------------------------------------- #
def test_crypto_always_open() -> None:
    s = CryptoSession()
    assert s.is_open(_ms(2026, 6, 13, 3, 0))  # Saturday 3am
    assert s.is_open(_ms(2026, 12, 25, 12, 0))  # Christmas
    assert s.next_open_ms(_ms(2026, 6, 13, 3, 0)) is None


# --------------------------------------------------------------------------- #
# US equities — regular-hours boundaries
# --------------------------------------------------------------------------- #
def test_equity_intraday_boundaries() -> None:
    s = UsEquitySession()
    # Wednesday 2026-06-10 is a normal trading day.
    assert not s.is_open(_ms(2026, 6, 10, 9, 29))  # one minute before the bell
    assert s.is_open(_ms(2026, 6, 10, 9, 30))      # open
    assert s.is_open(_ms(2026, 6, 10, 15, 59))     # last minute
    assert not s.is_open(_ms(2026, 6, 10, 16, 0))  # close is exclusive
    assert not s.is_open(_ms(2026, 6, 10, 20, 0))  # after hours


def test_equity_closed_on_weekend() -> None:
    s = UsEquitySession()
    assert not s.is_open(_ms(2026, 6, 13, 12, 0))  # Saturday
    assert not s.is_open(_ms(2026, 6, 14, 12, 0))  # Sunday


def test_equity_closed_on_holidays() -> None:
    s = UsEquitySession()
    # A weekday holiday: Juneteenth (Fri 2026-06-19) and Christmas (Fri 2026-12-25).
    assert not s.is_open(_ms(2026, 6, 19, 12, 0))
    assert not s.is_open(_ms(2026, 12, 25, 12, 0))
    # Independence Day 2026 falls on a Saturday → observed Friday Jul 3.
    assert not s.is_open(_ms(2026, 7, 3, 12, 0))


def test_equity_handles_dst() -> None:
    s = UsEquitySession()
    # Winter (EST, UTC-5) and summer (EDT, UTC-4) both open at 09:30 *local*.
    assert s.is_open(_ms(2026, 1, 5, 9, 30))   # Mon, EST
    assert s.is_open(_ms(2026, 7, 6, 9, 30))   # Mon, EDT
    assert not s.is_open(_ms(2026, 1, 5, 9, 29))


def test_equity_next_open_skips_weekend() -> None:
    s = UsEquitySession()
    # Friday 2026-06-12 17:00 → next open is Monday 2026-06-15 09:30 ET.
    nxt = s.next_open_ms(_ms(2026, 6, 12, 17, 0))
    assert nxt is not None
    got = datetime.fromtimestamp(nxt / 1000, tz=UTC).astimezone(_ET)
    assert (got.year, got.month, got.day, got.hour, got.minute) == (2026, 6, 15, 9, 30)


def test_holiday_set_covers_current_year() -> None:
    # Belt to force an annual refresh: the hardcoded NYSE set must cover this year.
    # When this fails, extend _NYSE_HOLIDAYS with the new year's dates.
    year = datetime.now(UTC).year
    in_year = [d for d in _NYSE_HOLIDAYS if d.year == year]
    assert len(in_year) >= 9, f"NYSE holidays for {year} look incomplete: {sorted(in_year)}"
