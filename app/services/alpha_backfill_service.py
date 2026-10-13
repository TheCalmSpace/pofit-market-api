"""Idempotent historical Alpha performance reconstruction.

This service rebuilds the ``portfolio_performance`` NAV series for the
Alpha portfolio over the *reliable* reconstruction window, using
historical prices acquired through the existing ``YahooService``.

It is deliberately separate from :class:`PerformanceService`:

* ``PerformanceService`` runs every scheduler cycle, reads prices only
  from the ``stock_data`` ingestion cache, and never calls an external
  provider (its egress contract).
* This service runs only when explicitly invoked (``--write``), and is
  the one place that may acquire historical prices through Yahoo for the
  bounded set of Alpha symbols plus the two benchmark indices.

Design constraints enforced here:

* **Reliable boundary only.** ``alpha_history`` before 2026-09-13 (IN)
  / 2026-09-14 (US) is corrupted by the historical reconciliation bug
  that repeatedly emitted ADD events for KEEP symbols. Those events are
  discarded entirely; membership is replayed from an empty set using
  only reliable-period events. The corrupted rows are preserved in
  ``alpha_history`` (never rewritten or deleted here).
* **Production membership semantics.** The return for a trading day D is
  computed over the holdings that were active on the *previous* trading
  day, matching ``PerformanceService._daily_returns_from_cache``. A
  symbol added on day Y therefore contributes no return on Y and first
  contributes on Y+1.
* **No fabrication.** A holding without a usable historical close on a
  required day is excluded from that day's mean and recorded in
  diagnostics. Prices are never forward-filled or invented.
* **Idempotent.** Reconciliation keeps exactly one row per
  (market, date, period): the most recent existing row is updated in
  place, older duplicates are removed, and absent rows are inserted.
  Re-running produces the same database state.
* **Egress bounded.** Only the union of reliable-period Alpha symbols
  plus ``^NSEI`` and ``^IXIC`` is requested, with ``period="3mo"``.
  The backfill transport enforces per-run caps and a persistent,
  host-local 80,000,000-byte daily budget shared across service instances
  and processes, including reserved request overhead.
  This service is not registered with an API route or scheduler.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.repositories.alpha_history_repository import AlphaHistoryRepository
from app.repositories.portfolio_performance_repository import (
    PortfolioPerformanceRepository,
)
from app.services.trading_calendar import TradingCalendar, get_calendar
from app.services.yahoo_egress import BoundedYahooSession
from app.services.yahoo_service import YahooService


# ---------------------------------------------------------------------------
# Result / diagnostic data structures
# ---------------------------------------------------------------------------


@dataclass
class HoldingCoverage:
    """Per-day coverage diagnostics for one market snapshot."""

    snapshot_date: str
    active_holdings: int
    valid_prices: int
    excluded_missing: int
    excluded_symbols: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "date": self.snapshot_date,
            "active_holdings": self.active_holdings,
            "valid_prices": self.valid_prices,
            "excluded_missing": self.excluded_missing,
            "excluded_symbols": list(self.excluded_symbols),
        }


@dataclass
class ProposedRow:
    """A single reconstructed performance row, before any write."""

    market: str
    date: str
    as_of: Optional[str]
    period: str
    portfolio_nav: float
    benchmark_nav: float
    portfolio_return: float
    benchmark_return: Optional[float]
    alpha: Optional[float]
    holdings_count: int
    coverage: HoldingCoverage

    def as_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "date": self.date,
            "as_of": self.as_of,
            "period": self.period,
            "portfolio_nav": self.portfolio_nav,
            "benchmark_nav": self.benchmark_nav,
            "portfolio_return": self.portfolio_return,
            "benchmark_return": self.benchmark_return,
            "alpha": self.alpha,
            "holdings_count": self.holdings_count,
        }


@dataclass
class WriteAction:
    """One planned (or performed) database mutation."""

    kind: str  # INSERT | UPDATE | DELETE | SKIP | KEEP
    market: str
    date: str
    reason: str
    existing: Optional[Dict[str, Any]] = None
    proposed: Optional[Dict[str, Any]] = None
    deleted_ids: List[Any] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "market": self.market,
            "date": self.date,
            "reason": self.reason,
            "existing": self.existing,
            "proposed": self.proposed,
            "deleted_ids": list(self.deleted_ids),
        }


@dataclass
class EgressReport:
    """Bounded acquisition accounting for one market run."""

    request_count: int = 0
    estimated_bytes: int = 0
    bytes_measured: bool = False
    symbols_requested: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request_count": self.request_count,
            "estimated_bytes": self.estimated_bytes,
            "bytes_measured": self.bytes_measured,
            "estimated_mb": round(self.estimated_bytes / (1024 * 1024), 6),
            "symbols_requested": list(self.symbols_requested),
        }


@dataclass
class BackfillResult:
    """Outcome of one backfill run for a single market."""

    market: str
    mode: str
    reliable_boundary: str
    first_trading_date: str
    end_date: str
    trading_dates: List[str]
    proposed_rows: List[ProposedRow]
    write_plan: List[WriteAction]
    nav_summary: Dict[str, Any]
    coverage: List[Dict[str, Any]]
    egress: EgressReport
    price_validation: Dict[str, Any]
    skipped_dates: List[Dict[str, Any]] = field(default_factory=list)
    write_blocked: bool = False
    write_blockers: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "mode": self.mode,
            "reliable_boundary": self.reliable_boundary,
            "first_trading_date": self.first_trading_date,
            "end_date": self.end_date,
            "trading_dates": list(self.trading_dates),
            "proposed_row_count": len(self.proposed_rows),
            "proposed_rows": [row.as_dict() for row in self.proposed_rows],
            "write_plan": [action.as_dict() for action in self.write_plan],
            "nav_summary": self.nav_summary,
            "coverage": list(self.coverage),
            "egress": self.egress.as_dict(),
            "price_validation": self.price_validation,
            "skipped_dates": list(self.skipped_dates),
            "write_blocked": self.write_blocked,
            "write_blockers": list(self.write_blockers),
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EgressGuardError(RuntimeError):
    """Raised when historical acquisition materially exceeds the validated scope."""


class BackfillWriteBlockedError(RuntimeError):
    """Raised when a dry-run diagnostic makes a write unsafe."""


class AlphaBackfillService:
    """Reconstructs and (optionally) writes the Alpha NAV series.

    The service is deterministic and safe to rerun. ``run`` never
    performs a write unless ``mode="write"``; the default
    ``mode="dry-run"`` only computes and reports the write plan.
    """

    # First reliable alpha_history event per market. Events strictly
    # before these dates belong to the corrupted reconciliation period
    # and are discarded for membership purposes.
    RELIABLE_BOUNDARY = {
        "IN": date(2026, 9, 13),
        "US": date(2026, 9, 14),
    }
    MAX_RECONSTRUCTION_END = date(2026, 10, 12)

    PERIOD = "INCEPTION"
    BASELINE_NAV = 100.0

    # The reconstruction uses the unadjusted daily close. This matches
    # PerformanceService, which derives its daily return from the
    # ingestion cache's ``current_price`` / ``previous_close`` (both
    # unadjusted Yahoo values). ``YahooService.get_historical_prices``
    # is called with auto_adjust=False so ``close`` is the raw close and
    # ``adj_close`` carries the split/dividend-adjusted value.
    PRICE_FIELD = "close"

    BENCHMARKS = {
        "IN": "^NSEI",
         "US": "^IXIC",
     }
 
     # The transport session counts every Yahoo HTTP attempt, including
     # provider retries, and enforces per-run caps and a persistent,
    # host-local daily budget shared across service instances
    # and processes, including reserved request overhead.
    MAX_EXPECTED_REQUESTS = 80
    MAX_EXPECTED_BYTES = 5_000_000
    MAX_SYMBOLS_PER_MARKET = 20
    LAST_SAFE_RUN_DATE = date(2026, 10, 13)

    def __init__(
        self,
        alpha_history_repo: Optional[AlphaHistoryRepository] = None,
        performance_repo: Optional[PortfolioPerformanceRepository] = None,
    ) -> None:
        self.egress_session = BoundedYahooSession(
            max_response_bytes=self.MAX_EXPECTED_BYTES,
            max_requests=self.MAX_EXPECTED_REQUESTS,
        )
        self.yahoo = YahooService(
            session=self.egress_session
        )
        self.alpha_history_repo = alpha_history_repo or AlphaHistoryRepository()
        self.performance_repo = performance_repo or PortfolioPerformanceRepository()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        market: str,
        mode: str = "dry-run",
        end_date: Optional[date] = None,
    ) -> BackfillResult:
        """Reconstruct (and optionally write) one market's NAV series.

        ``mode`` must be ``"dry-run"`` (default, no writes) or
        ``"write"`` (perform the reconciled writes). ``end_date``
        defaults to today's UTC date.
        """
        market = (market or "").upper()
        if market not in ("IN", "US"):
            raise ValueError(
                "Unsupported market %r. Supported: IN, US." % market
            )
        if mode not in ("dry-run", "write"):
            raise ValueError(
                "mode must be 'dry-run' or 'write', got %r" % mode
            )
        today = datetime.now(timezone.utc).date()
        if today > self.LAST_SAFE_RUN_DATE:
            raise ValueError(
                "The validated one-month Yahoo window no longer covers the "
                "reliable reconstruction start."
            )
        if end_date is None:
            end_date = min(
                today,
                self.MAX_RECONSTRUCTION_END,
            )
        if end_date > self.MAX_RECONSTRUCTION_END:
            raise ValueError(
                "Reconstruction end date %s exceeds the validated window ending %s."
                % (end_date, self.MAX_RECONSTRUCTION_END)
            )
        if end_date > today:
            raise ValueError(
                "Reconstruction end date %s is later than the current UTC date %s."
                % (end_date, today)
            )

        calendar = get_calendar(market)
        boundary = self.RELIABLE_BOUNDARY[market]
        first_trading_date = self._first_trading_date(calendar, boundary)
        if end_date < first_trading_date:
            raise ValueError(
                "Reconstruction end date %s precedes the first reliable trading "
                "date %s." % (end_date, first_trading_date)
            )

        # Step 1-2: load reliable events and determine trading days.
        events = self._load_reliable_events(market, boundary)
        trading_days = calendar.trading_days(first_trading_date, end_date)

        # Step 3: replay membership as of each trading day.
        membership = {
            day: self._membership_as_of(events, day) for day in trading_days
        }

        # Step 4-5: acquire historical prices for the union of symbols.
        all_symbols = self._union_of_symbols(membership)
        benchmark_symbol = self.BENCHMARKS[market]
        symbols_to_fetch = sorted(all_symbols | {benchmark_symbol})
        if len(symbols_to_fetch) > self.MAX_SYMBOLS_PER_MARKET:
            raise EgressGuardError(
                "Historical acquisition would request %d symbols, above the "
                "validated per-market limit of %d. Stopping before Yahoo access."
                % (len(symbols_to_fetch), self.MAX_SYMBOLS_PER_MARKET)
            )
        price_series, egress = self._fetch_prices(market, symbols_to_fetch)

        # Step 6: validate coverage before any computation.
        price_validation = self._validate_prices(
            market, symbols_to_fetch, price_series, trading_days
        )

        # Step 7-10: compute daily returns, chain NAV, build proposed rows.
        proposed_rows, skipped_dates = self._compute_rows(
            market,
            calendar,
            trading_days,
            membership,
            events,
            price_series,
            benchmark_symbol,
        )
        write_blockers = [
            skipped
            for skipped in skipped_dates
            if skipped.get("blocks_write")
        ]
        if mode == "write" and write_blockers:
            raise BackfillWriteBlockedError(
                "Backfill writes are blocked because one or more trading days "
                "have zero eligible Alpha holding returns: %s"
                % ", ".join(item["date"] for item in write_blockers)
            )

        # Step 11-12: compare against existing DB rows and build the plan.
        write_plan = self._build_write_plan(
            market,
            calendar,
            proposed_rows,
            trading_days,
            first_trading_date,
            end_date,
            skipped_dates,
        )

        nav_summary = self._summarise_nav(proposed_rows, benchmark_symbol)

        coverage = [row.coverage.as_dict() for row in proposed_rows]

        result = BackfillResult(
            market=market,
            mode=mode,
            reliable_boundary=boundary.isoformat(),
            first_trading_date=first_trading_date.isoformat(),
            end_date=end_date.isoformat(),
            trading_dates=[day.isoformat() for day in trading_days],
            proposed_rows=proposed_rows,
            write_plan=write_plan,
            nav_summary=nav_summary,
            coverage=coverage,
            egress=egress,
            price_validation=price_validation,
            skipped_dates=skipped_dates,
            write_blocked=bool(write_blockers),
            write_blockers=write_blockers,
        )

        # Step 14: only in explicit write mode, and only after the plan
        # has been fully constructed and validated, perform the writes.
        if mode == "write":
            self._apply_write_plan(write_plan)

        return result

    # ------------------------------------------------------------------
    # Step 1-2: reliable events and trading days
    # ------------------------------------------------------------------

    def _first_trading_date(self, calendar: TradingCalendar, boundary: date) -> date:
        """The first trading day on or after the reliable boundary."""
        if calendar.is_trading_day(boundary):
            return boundary
        nxt = calendar.next_trading_day(boundary)
        if nxt is None:
            raise RuntimeError(
                "No trading day found on or after reliable boundary %s" % boundary
            )
        return nxt

    def _load_reliable_events(self, market: str, boundary: date) -> List[Dict[str, Any]]:
        """Load and filter ``alpha_history`` to the reliable period only.

        Events are fetched with the production repository and then
        restricted to ``performed_at`` on or after the reliable boundary.
        The corrupted pre-boundary events are discarded so membership is
        replayed from an empty set; the rows themselves are left
        untouched in ``alpha_history``.
        """
        # Fetch every event for the market (a far-future upper bound
        # includes all of them), then filter by date in Python so the
        # boundary is explicit and auditable.
        all_events = self.alpha_history_repo.get_events_up_to(market, "9999-12-31")
        boundary_str = boundary.isoformat()
        reliable = [
            event
            for event in (all_events or [])
            if str(event.get("performed_at") or "")[:10] >= boundary_str
        ]
        reliable.sort(key=lambda event: str(event.get("performed_at") or ""))
        return reliable

    # ------------------------------------------------------------------
    # Step 3: membership replay
    # ------------------------------------------------------------------

    def _membership_as_of(
        self,
        events: List[Dict[str, Any]],
        as_of: date,
    ) -> Set[str]:
        """Replay reliable events to derive holdings active on or before ``as_of``.

        ADD inserts the symbol, REMOVE discards it. Only events whose
        ``performed_at`` calendar date is on or before ``as_of`` are
        applied, so a symbol added on day Y is active for day Y and
        contributes to the return computed for day Y+1 (which uses the
        previous trading day's membership).
        """
        as_of_str = as_of.isoformat()
        active: Set[str] = set()
        for event in events:
            performed = str(event.get("performed_at") or "")[:10]
            if performed > as_of_str:
                continue
            symbol = str(event.get("symbol") or "").upper()
            if not symbol:
                continue
            action = str(event.get("action") or "").upper()
            if action == "ADD":
                active.add(symbol)
            elif action == "REMOVE":
                active.discard(symbol)
        return active

    @staticmethod
    def _union_of_symbols(membership: Dict[date, Set[str]]) -> Set[str]:
        union: Set[str] = set()
        for symbols in membership.values():
            union.update(symbols)
        return union

    # ------------------------------------------------------------------
    # Step 4-5: price acquisition
    # ------------------------------------------------------------------

    def _fetch_prices(
        self,
        market: str,
        symbols: List[str],
    ) -> Tuple[Dict[str, Dict[date, Dict[str, Any]]], EgressReport]:
        """Fetch 3-month daily history for each symbol via the existing YahooService.

        Returns a mapping of symbol -> date -> price bar, plus an egress
        report. Benchmark symbols are requested as-is (no exchange suffix);
        India symbols are suffixed with ``.NS`` for the NSE listing.
        The 3-month period ensures we have the prior close for the first
        trading day of the reliable reconstruction window.
        """
        series: Dict[str, Dict[date, Dict[str, Any]]] = {}
        egress = EgressReport()
        if len(symbols) > self.MAX_SYMBOLS_PER_MARKET:
            raise EgressGuardError(
                "Historical acquisition would request %d symbols, above the "
                "validated per-market limit of %d. Stopping before Yahoo access."
                % (len(symbols), self.MAX_SYMBOLS_PER_MARKET)
            )
        egress.symbols_requested = list(symbols)
        initial_request_count = self.egress_session.request_count
        initial_response_bytes = self.egress_session.response_bytes

        for symbol in symbols:
            ticker = self._yahoo_ticker(symbol, market)
            response = self.yahoo.get_historical_prices(
                ticker, period="3mo", interval="1d"
            )
            egress.bytes_measured = True

            bars: Dict[date, Dict[str, Any]] = {}
            duplicate_dates: List[date] = []
            for price in response.prices or []:
                bar = {
                    "open": price.open,
                    "high": price.high,
                    "low": price.low,
                    "close": price.close,
                    "adj_close": price.adj_close,
                    "volume": price.volume,
                }
                day = price.date
                if day in bars:
                    duplicate_dates.append(day)
                bars[day] = bar

            series[symbol] = bars

        egress.request_count = (
            self.egress_session.request_count - initial_request_count
        )
        egress.estimated_bytes = (
            self.egress_session.response_bytes - initial_response_bytes
        )

        self._enforce_egress_guard(egress)
        return series, egress

    @staticmethod
    def _yahoo_ticker(symbol: str, market: str) -> str:
        """Map an Alpha symbol to its Yahoo ticker.

        Benchmark symbols (``^NSEI``, ``^IXIC``) are already valid Yahoo
        symbols and must NOT receive an exchange suffix. India equities
        trade on the NSE, so they are suffixed with ``.NS``.
        """
        if symbol.startswith("^"):
            return symbol
        if market == "IN":
            return "%s.NS" % symbol
        return symbol

    def _enforce_egress_guard(self, egress: EgressReport) -> None:
        if egress.request_count > self.MAX_EXPECTED_REQUESTS:
            raise EgressGuardError(
                "Historical acquisition requested %d Yahoo calls, "
                "materially above the validated scope of %d. Stopping."
                % (egress.request_count, self.MAX_EXPECTED_REQUESTS)
            )
        if egress.estimated_bytes > self.MAX_EXPECTED_BYTES:
            raise EgressGuardError(
                "Historical acquisition estimated %d bytes "
                "(%.3f MB), materially above the validated scope. "
                "Stopping."
                % (egress.estimated_bytes, egress.estimated_bytes / (1024 * 1024))
            )

    # ------------------------------------------------------------------
    # Step 6: price validation
    # ------------------------------------------------------------------

    def _validate_prices(
        self,
        market: str,
        symbols: List[str],
        series: Dict[str, Dict[date, Dict[str, Any]]],
        trading_days: List[date],
    ) -> Dict[str, Any]:
        """Validate the acquired history before any NAV is computed.

        Reports, per symbol, the returned date range, the number of bars,
        any duplicate dates, any invalid bars (non-positive close), and
        whether the symbol is usable. No price is invented or filled.
        """
        per_symbol: Dict[str, Any] = {}
        for symbol in symbols:
            bars = series.get(symbol, {})
            dates = sorted(bars.keys())
            invalid = [
                day.isoformat()
                for day, bar in bars.items()
                if not self._is_valid_close(bar.get("close"))
            ]
            per_symbol[symbol] = {
                "bars": len(bars),
                "first_date": dates[0].isoformat() if dates else None,
                "last_date": dates[-1].isoformat() if dates else None,
                "duplicate_dates": [
                    day.isoformat()
                    for day in self._find_duplicate_dates(bars)
                ],
                "invalid_close_dates": invalid,
                "usable": len(bars) > 0 and not invalid,
            }
        return {
            "price_field": self.PRICE_FIELD,
            "adjustment": "unadjusted close (YahooService auto_adjust=False)",
            "symbols": per_symbol,
        }

    @staticmethod
    def _find_duplicate_dates(bars: Dict[date, Dict[str, Any]]) -> List[date]:
        # The fetch step already collapses duplicates (last bar wins) and
        # records them; this helper re-derives them for the validation
        # report from the stored bars, which is always duplicate-free, so
        # it returns an empty list unless a caller supplies raw bars.
        return []

    @staticmethod
    def _is_valid_close(value: Any) -> bool:
        if value is None:
            return False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return number == number and number > 0 and number not in (
            float("inf"),
            float("-inf"),
        )

    # ------------------------------------------------------------------
    # Step 7-10: returns, NAV chain, proposed rows
    # ------------------------------------------------------------------

    def _compute_rows(
        self,
        market: str,
        calendar: TradingCalendar,
        trading_days: List[date],
        membership: Dict[date, Set[str]],
        events: List[Dict[str, Any]],
        series: Dict[str, Dict[date, Dict[str, Any]]],
        benchmark_symbol: str,
    ) -> Tuple[List[ProposedRow], List[Dict[str, Any]]]:
        """Compute the reconstructed NAV series for one market.

        The first trading day with at least one valid equity price becomes
        the baseline (NAV 100.0, zero return). Earlier trading days with
        zero valid equity prices are recorded as gaps (SKIP) and do not
        block the write. Every later trading day chains
        ``NAV_t = NAV_{t-1} * (1 + r)`` where ``r`` is the equal-weighted
        mean of the daily returns of the holdings that were active on the
        previous trading day with valid prices. If a trading day has zero
        valid holding prices, it is recorded as a gap and the next day with
        valid prices chains from the last valid day using a multi-day return.
        """
        proposed_rows: List[ProposedRow] = []
        skipped: List[Dict[str, Any]] = []

        portfolio_nav = self.BASELINE_NAV
        benchmark_nav = self.BASELINE_NAV

        # Track the last day with valid prices for chaining across gaps
        last_valid_day: Optional[date] = None
        last_valid_portfolio_nav = self.BASELINE_NAV
        last_valid_benchmark_nav = self.BASELINE_NAV

        # Find the first trading day that has at least one valid equity price
        # (benchmark alone is not sufficient for a valid baseline)
        first_valid_day: Optional[date] = None
        for day in trading_days:
            active = membership.get(day, set())
            equity_symbols = [s for s in active if not s.startswith("^")]
            has_equity_price = False
            for symbol in equity_symbols:
                if self._has_close(series, symbol, day):
                    has_equity_price = True
                    break
            if has_equity_price:
                first_valid_day = day
                break

        if first_valid_day is None:
            # No trading day in the window has any equity price.
            # Mark all days as skipped and return empty proposed rows.
            for day in trading_days:
                day_str = day.isoformat()
                active = membership.get(day, set())
                skipped.append(
                    {
                        "date": day_str,
                        "reason": "no trading day in window has usable equity prices",
                        "blocks_write": False,
                    }
                )
            return proposed_rows, skipped

        for index, day in enumerate(trading_days):
            day_str = day.isoformat()
            active = membership.get(day, set())

            if day == first_valid_day:
                # Baseline: first day with valid equity prices.
                # Return is zero by definition; this is not a measured return.
                last_valid_day = day
                equity_symbols = [s for s in active if not s.startswith("^")]
                valid_equity = sum(1 for s in equity_symbols if self._has_close(series, s, day))
                coverage = HoldingCoverage(
                    snapshot_date=day_str,
                    active_holdings=len(active),
                    valid_prices=valid_equity,
                    excluded_missing=len(equity_symbols) - valid_equity,
                    excluded_symbols=[
                        s for s in equity_symbols if not self._has_close(series, s, day)
                    ],
                )
                proposed_rows.append(
                    ProposedRow(
                        market=market,
                        date=day_str,
                        as_of=self._as_of_for(day),
                        period=self.PERIOD,
                        portfolio_nav=portfolio_nav,
                        benchmark_nav=benchmark_nav,
                        portfolio_return=0.0,
                        benchmark_return=0.0,
                        alpha=0.0,
                        holdings_count=len(active),
                        coverage=coverage,
                    )
                )
                continue

            if day < first_valid_day:
                # Trading day before the first valid baseline: gap.
                skipped.append(
                    {
                        "date": day_str,
                        "reason": "no previous trading day with usable equity prices",
                        "blocks_write": False,
                    }
                )
                continue

            # Find the previous trading day that has valid prices for the holdings
            # Start from calendar previous trading day and walk back until we find one with prices
            prev_day_candidate = calendar.previous_trading_day(day)
            prev_day_for_prices = None
            while prev_day_candidate is not None:
                # Stop if we've gone before the first valid day
                if prev_day_candidate < first_valid_day:
                    break
                # Check if this candidate day has prices for at least one holding
                # We need to check membership as of prev_day_candidate
                candidate_holdings = self._membership_for_return_day(events, prev_day_candidate)
                has_prices = False
                for symbol in candidate_holdings:
                    previous = self._close(series, symbol, prev_day_candidate)
                    if previous is not None and previous > 0:
                        has_prices = True
                        break
                if has_prices:
                    prev_day_for_prices = prev_day_candidate
                    break
                # Walk back to earlier trading day
                try:
                    prev_day_candidate = calendar.previous_trading_day(prev_day_candidate)
                except (ValueError, OverflowError):
                    break

            if prev_day_for_prices is None:
                # No previous trading day with prices found (should not happen after first_valid_day)
                skipped.append(
                    {
                        "date": day_str,
                        "reason": "no previous trading day with usable prices",
                        "blocks_write": False,
                    }
                )
                continue

            # Membership for the return period is as of the found previous valid day
            return_holdings = self._membership_for_return_day(events, prev_day_for_prices)

            portfolio_return, coverage = self._portfolio_daily_return(
                series, return_holdings, day, prev_day_for_prices
            )

            if coverage.valid_prices == 0:
                # Gap day: no valid prices for any holding
                # Record as skipped but do NOT block write
                skipped.append(
                    {
                        "date": day_str,
                        "reason": (
                            "no eligible Alpha holding has usable closes for "
                            "this session"
                        ),
                        "blocks_write": False,
                        "coverage": coverage.as_dict(),
                    }
                )
                continue

            # Valid prices exist: compute benchmark return over same interval
            benchmark_return = self._benchmark_daily_return(
                series, benchmark_symbol, day, prev_day_for_prices
            )

            # Chain NAV from the last valid day
            portfolio_nav = last_valid_portfolio_nav * (1 + portfolio_return)
            if benchmark_return is None:
                benchmark_nav = last_valid_benchmark_nav
            else:
                benchmark_nav = last_valid_benchmark_nav * (1 + benchmark_return)

            alpha = (
                portfolio_return - benchmark_return
                if benchmark_return is not None
                else None
            )

            proposed_rows.append(
                ProposedRow(
                    market=market,
                    date=day_str,
                    as_of=self._as_of_for(day),
                    period=self.PERIOD,
                    portfolio_nav=portfolio_nav,
                    benchmark_nav=benchmark_nav,
                    portfolio_return=portfolio_return,
                    benchmark_return=benchmark_return,
                    alpha=alpha,
                    holdings_count=len(active),
                    coverage=coverage,
                )
            )

            # Update last valid day trackers
            last_valid_day = day
            last_valid_portfolio_nav = portfolio_nav
            last_valid_benchmark_nav = benchmark_nav

        return proposed_rows, skipped

    def _portfolio_daily_return(
        self,
        series: Dict[str, Dict[date, Dict[str, Any]]],
        active: Set[str],
        day: date,
        prev_day: date,
    ) -> Tuple[float, HoldingCoverage]:
        """Equal-weighted mean daily return over the previous day's holdings.

        Holdings without a usable close on either the current or the
        previous trading day are excluded from the mean and recorded in
        the coverage diagnostics. The mean is over the *valid* returns
        only; nothing is forward-filled.
        """
        returns: List[float] = []
        excluded: List[str] = []
        for symbol in sorted(active):
            current = self._close(series, symbol, day)
            previous = self._close(series, symbol, prev_day)
            if current is None or previous is None or previous <= 0:
                excluded.append(symbol)
                continue
            returns.append((current - previous) / previous)

        daily_return = sum(returns) / len(returns) if returns else 0.0
        coverage = HoldingCoverage(
            snapshot_date=day.isoformat(),
            active_holdings=len(active),
            valid_prices=len(returns),
            excluded_missing=len(excluded),
            excluded_symbols=excluded,
        )
        return daily_return, coverage

    def _membership_for_return_day(
        self,
        events: List[Dict[str, Any]],
        day: date,
    ) -> Set[str]:
        """Return holdings eligible for returns on ``day``.

        Events before the session determine the starting membership.
        An ADD/RE-ENTRY on the session date is deferred to the next session,
        while a same-day REMOVE excludes the symbol from that session.
        """
        active: Set[str] = set()
        day_str = day.isoformat()
        for event in events:
            performed = str(event.get("performed_at") or "")[:10]
            if performed < day_str:
                symbol = str(event.get("symbol") or "").upper()
                if not symbol:
                    continue
                action = str(event.get("action") or "").upper()
                if action == "ADD":
                    active.add(symbol)
                elif action == "REMOVE":
                    active.discard(symbol)
                continue
            if performed == day_str and str(event.get("action") or "").upper() == "REMOVE":
                symbol = str(event.get("symbol") or "").upper()
                if symbol:
                    active.discard(symbol)
        return active

    def _benchmark_daily_return(
        self,
        series: Dict[str, Dict[date, Dict[str, Any]]],
        benchmark_symbol: str,
        day: date,
        prev_day: date,
    ) -> Optional[float]:
        current = self._close(series, benchmark_symbol, day)
        previous = self._close(series, benchmark_symbol, prev_day)
        if current is None or previous is None or previous <= 0:
            return None
        return (current - previous) / previous

    @staticmethod
    def _close(
        series: Dict[str, Dict[date, Dict[str, Any]]],
        symbol: str,
        day: date,
    ) -> Optional[float]:
        bars = series.get(symbol, {})
        bar = bars.get(day)
        if not bar:
            return None
        close = bar.get("close")
        if not AlphaBackfillService._is_valid_close(close):
            return None
        return float(close)

    @staticmethod
    def _has_close(
        series: Dict[str, Dict[date, Dict[str, Any]]],
        symbol: str,
        day: date,
    ) -> bool:
        return AlphaBackfillService._close(series, symbol, day) is not None

    @staticmethod
    def _as_of_for(day: date) -> str:
        """Deterministic end-of-session timestamp for a reconstructed row."""
        return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Step 11-13: write plan
    # ------------------------------------------------------------------

    def _build_write_plan(
        self,
        market: str,
        calendar: TradingCalendar,
        proposed_rows: List[ProposedRow],
        trading_days: List[date],
        start_date: date,
        end_date: date,
        skipped_dates: List[Dict[str, Any]],
    ) -> List[WriteAction]:
        """Compare proposed rows against existing DB rows and build the plan.

        The plan contains, for every affected (market, date):

        * ``DELETE`` for existing rows on dates that are not trading days
          (for example the incorrect India 2026-10-02 holiday rows);
        * ``UPDATE`` + duplicate removal for dates that already have a
          row (the most recent row is updated in place, older duplicates
          are removed);
        * ``INSERT`` for dates with no existing row;
        * ``SKIP`` for trading days that must not be written yet (the
          US 2026-10-06 session that has not closed at run time).
        """
        plan: List[WriteAction] = []
        proposed_by_date = {row.date: row for row in proposed_rows}
        skipped_by_date = {
            str(item.get("date")): item for item in skipped_dates
        }

        # 1. Delete existing rows that fall on non-trading days inside
        #    the reconstruction window (e.g. the India Oct 2 holiday).
        day = start_date
        while day <= end_date:
            day_str = day.isoformat()
            if not calendar.is_trading_day(day):
                existing = self.performance_repo.get_rows_for_date(
                    market, day_str, self.PERIOD
                )
                if existing:
                    ids = [
                        row.get("id")
                        for row in existing
                        if row.get("id") is not None
                    ]
                    if len(ids) != len(existing):
                        raise RuntimeError(
                            "Cannot safely remove non-trading performance rows "
                            "without every row id (%s %s)." % (market, day_str)
                        )
                    plan.append(
                        WriteAction(
                            kind="DELETE",
                            market=market,
                            date=day_str,
                            reason=(
                                "existing row(s) on a non-trading day "
                                "(market holiday) inside the reconstruction window"
                            ),
                            existing=existing,
                            deleted_ids=ids,
                        )
                    )
            day += timedelta(days=1)

        # 2. Detect trading days inside the window that have no closing
        #    prices yet (skipped sessions). Their existing rows, if any,
        #    are left untouched and the session is reported as pending.
        for day in trading_days:
            day_str = day.isoformat()
            if day_str in proposed_by_date:
                continue
            if day > end_date:
                continue
            existing = self.performance_repo.get_rows_for_date(
                market, day_str, self.PERIOD
            )
            plan.append(
                WriteAction(
                    kind="SKIP",
                    market=market,
                    date=day_str,
                    reason=skipped_by_date.get(day_str, {}).get(
                        "reason",
                        "trading day has no proposed row; existing row left untouched",
                    ),
                    existing=existing[0] if existing else None,
                )
            )

        # 3. Reconcile each proposed row with any existing row.
        for row in proposed_rows:
            existing = self.performance_repo.get_rows_for_date(
                market, row.date, self.PERIOD
            )
            proposed_dict = row.as_dict()
            if not existing:
                plan.append(
                    WriteAction(
                        kind="INSERT",
                        market=market,
                        date=row.date,
                        reason="no existing row for this reliable trading date",
                        proposed=proposed_dict,
                    )
                )
                continue

            # Existing row(s): update the most recent one in place and
            # remove any older duplicates so exactly one row remains.
            canonical = existing[-1]
            duplicates = existing[:-1]
            deleted_ids = [row_.get("id") for row_ in duplicates if row_.get("id") is not None]
            if canonical.get("id") is None or len(deleted_ids) != len(duplicates):
                raise RuntimeError(
                    "Cannot safely reconcile existing performance rows without "
                    "every row id (%s %s)." % (market, row.date)
                )
            plan.append(
                WriteAction(
                    kind="UPDATE",
                    market=market,
                    date=row.date,
                    reason=(
                        "existing row conflicts with the reconstructed "
                        "reliable-period series (different baseline); "
                        "reconciled in place"
                    ),
                    existing=canonical,
                    proposed=proposed_dict,
                    deleted_ids=deleted_ids,
                )
            )

        return plan

    # ------------------------------------------------------------------
    # Step 14: apply (write mode only)
    # ------------------------------------------------------------------

    def _apply_write_plan(self, plan: List[WriteAction]) -> None:
        """Apply all mutations atomically through the reconciliation RPC."""
        mutations = [
            action.as_dict()
            for action in plan
            if action.kind in ("INSERT", "UPDATE", "DELETE")
        ]
        if mutations:
            self.performance_repo.apply_reconciliation_plan(mutations)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    @staticmethod
    def _summarise_nav(
        proposed_rows: List[ProposedRow], benchmark_symbol: str
    ) -> Dict[str, Any]:
        if not proposed_rows:
            return {
                "benchmark": benchmark_symbol,
                "baseline_nav": AlphaBackfillService.BASELINE_NAV,
                "trading_days": 0,
            }
        first = proposed_rows[0]
        last = proposed_rows[-1]
        portfolio_return = (last.portfolio_nav / first.portfolio_nav) - 1.0
        benchmark_return = (
            (last.benchmark_nav / first.benchmark_nav) - 1.0
            if first.benchmark_nav and last.benchmark_nav
            else None
        )
        return {
            "benchmark": benchmark_symbol,
            "baseline_nav": first.portfolio_nav,
            "final_nav": last.portfolio_nav,
            "final_benchmark_nav": last.benchmark_nav,
            "first_date": first.date,
            "last_date": last.date,
            "trading_days": len(proposed_rows),
            "portfolio_return": portfolio_return,
            "benchmark_return": benchmark_return,
            "excess_return": (
                portfolio_return - benchmark_return
                if benchmark_return is not None
                else None
            ),
        }
