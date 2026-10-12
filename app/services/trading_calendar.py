"""Market-specific trading calendars for the Alpha performance
reconstruction.

The daily performance snapshot must step once per *trading* day for
the market it describes. A single global calendar that only skips
weekends is wrong for India: the NSE closes on public holidays such
as Gandhi Jayanti (2026-10-02), and a snapshot written on a holiday
chains a return that was never earned, corrupting the NAV series.

This module therefore keeps an explicit, per-market holiday set and
exposes a small, independently testable calendar API. Each market
owns its own calendar; there is deliberately no shared global
calendar, so a holiday that closes the NSE does not close NASDAQ.

The holiday sets below cover the reliable reconstruction window
(2026-09-13 .. 2026-10-06). They are explicit rather than guessed
so that the reconstruction boundary is auditable; additional
holidays can be added to the frozensets as the window grows.
"""

from datetime import date, timedelta
from typing import FrozenSet, Iterable, List, Optional

# NSE (India) public holidays inside the reconstruction window.
# 2026-10-02 is Gandhi Jayanti: the NSE is closed, so no India
# performance row may exist for that date.
NSE_HOLIDAYS: FrozenSet[date] = frozenset(
    {
        date(2026, 10, 2),  # Gandhi Jayanti
    }
)

# US (NASDAQ / NYSE) holidays inside the reconstruction window.
# Labor Day 2026 fell on 2026-09-07 (before the reliable boundary)
# and Columbus Day 2026 falls on 2026-10-12 (after it), so there
# are no US market holidays between 2026-09-14 and 2026-10-06.
US_HOLIDAYS: FrozenSet[date] = frozenset()

_MARKET_HOLIDAYS = {
    "IN": NSE_HOLIDAYS,
    "US": US_HOLIDAYS,
}

SUPPORTED_MARKETS = ("IN", "US")


class TradingCalendar:
    """A market-specific trading-day calendar.

    The calendar answers three questions for a single market:

    * is a given date a trading day?
    * what are the trading days in a date range?
    * what is the previous / next trading day relative to a date?

    Weekends are always excluded. Market holidays are excluded in
    addition to weekends. The holiday set is injected so the calendar
    can be unit-tested without touching production data.
    """

    def __init__(
        self,
        market: str,
        holidays: Optional[Iterable[date]] = None,
    ) -> None:
        self.market = (market or "").upper()
        if self.market not in SUPPORTED_MARKETS:
            raise ValueError(
                "Unsupported market %r. Supported: %s."
                % (market, ", ".join(SUPPORTED_MARKETS))
            )
        if holidays is None:
            holidays = _MARKET_HOLIDAYS[self.market]
        self._holidays = frozenset(holidays)

    @property
    def holidays(self) -> frozenset:
        return self._holidays

    def is_trading_day(self, day: date) -> bool:
        """True when `day` is a trading day for this market."""
        if day is None:
            return False
        return day.weekday() < 5 and day not in self._holidays

    def trading_days(self, start: date, end: date) -> List[date]:
        """All trading days in the inclusive range [start, end]."""
        if start is None or end is None or end < start:
            return []
        days: List[date] = []
        cursor = start
        while cursor <= end:
            if self.is_trading_day(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return days

    def previous_trading_day(self, day: date) -> Optional[date]:
        """The closest trading day strictly before `day`, or None."""
        if day is None:
            return None
        cursor = day - timedelta(days=1)
        # Search back at most a week; a market is never closed for a
        # full week, so this always terminates.
        for _ in range(7):
            if self.is_trading_day(cursor):
                return cursor
            cursor -= timedelta(days=1)
        return None

    def next_trading_day(self, day: date) -> Optional[date]:
        """The closest trading day strictly after `day`, or None."""
        if day is None:
            return None
        cursor = day + timedelta(days=1)
        for _ in range(7):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        return None


def get_calendar(market: str) -> TradingCalendar:
    """Return the production calendar for `market`."""
    return TradingCalendar(market)
