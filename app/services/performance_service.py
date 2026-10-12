from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import json

from app.repositories.alpha_history_repository import AlphaHistoryRepository
from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.repositories.portfolio_performance_repository import (
	PortfolioPerformanceRepository,
)
from app.repositories.stock_data_repository import StockDataRepository
from app.services.trading_calendar import TradingCalendar, get_calendar
from app.utils.symbol import MARKET_BENCHMARKS

# Bound at import time so `isinstance` checks keep working in tests that
# patch the module-level `datetime`.
_DATE_TYPE = date
_DATETIME_TYPE = datetime


def _as_positive_float(value: Any) -> Optional[float]:
	"""Return `value` as a positive float, or None if it is unusable."""
	if value is None:
		return None
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	if number != number or number in (float("inf"), float("-inf")):
		return None
	return number if number > 0 else None


def _is_from_trading_day(updated_at: Any, snapshot_date: str) -> bool:
	"""True when a cached row was refreshed on the snapshot's trading day.

	A cached row from an earlier day describes a different session. Chaining
	its return would compound stale data, so such rows are skipped instead.
	"""
	if not updated_at:
		return False
	try:
		text = str(updated_at)
	except Exception:
		return False
	return text.split("T", 1)[0][:10] == snapshot_date


class PerformanceService:
	"""
	Computes daily portfolio performance snapshots and stores them
	in the `portfolio_performance` table as a continuous NAV series.

	Approach:
	- Each daily snapshot stores portfolio_nav and benchmark_nav.
	- NAV is updated incrementally: NAV_t = NAV_{t-1} * (1 + daily_return)
	- Portfolio daily return includes ONLY stocks that were in the
	  portfolio on the previous trading session (verified via alpha_history).
	- New stocks contribute no return on their entry day.
	- Benchmark NAV follows the same pattern for NIFTY 50 / NASDAQ.
	- Historical reports compute period returns from the stored NAV series:
	  period_return = (NAV_end / NAV_start) - 1

	EGRESS CONTRACT (hard):
	This service performs database reads, calculations, and database
	writes ONLY. It holds no market-data provider and never calls Yahoo,
	NSE, or any other external endpoint. Every price is read from the
	existing `stock_data.quote_json` ingestion cache, which is already
	populated by the bounded stock ingestion job. Adding a provider here
	would raise network egress, so it is intentionally absent.

	The benchmark index is read from that same cache and from no other
	source. Its row is written by the quote-only benchmark refresh that runs
	once per market immediately before this snapshot; the holdings' rows are
	written by the bounded ingestion and Top Picks quote refreshes. This
	service only ever reads, so a missing benchmark row leaves the benchmark
	NAV unchanged and reports `benchmark_status="missing"` rather than
	reaching outside the database for a price.
	"""

	# Shared with the benchmark quote refresh so both sides of the cache agree
	# on which index each market is measured against. This is a plain mapping:
	# it carries no provider, so the egress contract above still holds.
	BENCHMARKS = MARKET_BENCHMARKS
	# Reliable reconstruction boundary: first trading day with verifiable data.
	# This is NOT the product launch date; it is the earliest date with
	# complete, validated price and membership data.
	RELIABLE_INCEPTION_START = {
		"IN": date(2026, 9, 14),
		"US": date(2026, 9, 14),
	}

	# Period definitions in trading days (not calendar days)
	PERIOD_TRADING_DAYS = {
		"1m": 21,    # ~1 month of trading days
		"3m": 63,    # ~3 months of trading days
		"6m": 126,   # ~6 months of trading days
		"1y": 252,   # ~1 year of trading days
	}

	FY_START = date(2025, 4, 1)
	FY_END = date(2026, 3, 31)

	def __init__(self):
		self.alpha_repo = AlphaPortfolioRepository()
		self.alpha_history_repo = AlphaHistoryRepository()
		self.performance_repo = PortfolioPerformanceRepository()
		self.stock_data_repo = StockDataRepository()
		self._calendars: Dict[str, TradingCalendar] = {}

	def _get_calendar(self, market: str) -> TradingCalendar:
		"""Get or create the trading calendar for a market."""
		market = (market or "").upper()
		if not hasattr(self, "_calendars") or self._calendars is None:
			self._calendars = {}
		if market not in self._calendars:
			self._calendars[market] = get_calendar(market)
		return self._calendars[market]


	def snapshot_market(self, market: str) -> Optional[Dict[str, Any]]:
		market = (market or "").upper()
		now = datetime.utcnow()
		as_of = now.isoformat()
		snapshot_date = now.date().isoformat()

		rows = self.alpha_repo.get_all(market)
		last = self.performance_repo.get_latest(market)

		prev_nav = (
			100.0
			if last is None
			else float(last.get("portfolio_nav") or 100.0)
		)
		prev_bench_nav = (
			100.0
			if last is None
			else float(last.get("benchmark_nav") or 100.0)
		)

		diagnostics: Dict[str, Any] = {
			"missing_prices": [],
			"stale_prices": [],
			"benchmark_status": None,
			"nav_step": "baseline",
		}

		portfolio_nav = prev_nav
		benchmark_nav = prev_bench_nav

		if not rows:
			diagnostics["nav_step"] = "no_holdings"
		elif last is None:
			diagnostics["nav_step"] = "baseline"
		elif self._snapshot_date_of(last) == snapshot_date:
			# The NAV series steps once per trading day. Re-running the
			# snapshot later the same day must not chain a second daily
			# return on top of the first, so the value carries forward.
			diagnostics["nav_step"] = "already_recorded_for_date"
		else:
			# Get calendar if available for this market
			try:
				calendar = self._get_calendar(market)
				today = now.date()
				if not calendar.is_trading_day(today):
					diagnostics["nav_step"] = "non_trading_day"
				else:
					diagnostics["nav_step"] = "daily_return"
					portfolio_daily_return, benchmark_daily_return = (
						self._daily_returns_from_cache(
							rows, market, snapshot_date, diagnostics
						)
					)
					portfolio_nav = prev_nav * (1 + portfolio_daily_return)
					if benchmark_daily_return is None:
						benchmark_nav = prev_bench_nav
					else:
						benchmark_nav = prev_bench_nav * (1 + benchmark_daily_return)
			except ValueError:
				# Unsupported market - proceed without calendar validation
				diagnostics["nav_step"] = "daily_return"
				portfolio_daily_return, benchmark_daily_return = (
					self._daily_returns_from_cache(
						rows, market, snapshot_date, diagnostics
					)
				)
				portfolio_nav = prev_nav * (1 + portfolio_daily_return)
				if benchmark_daily_return is None:
					benchmark_nav = prev_bench_nav
				else:
					benchmark_nav = prev_bench_nav * (1 + benchmark_daily_return)

		payload = {
			"market": market,
			"date": snapshot_date,
			"as_of": as_of,
			"period": "INCEPTION",
			"portfolio_nav": portfolio_nav,
			"benchmark_nav": benchmark_nav,
			"holdings_count": len(rows),
		}

		self.performance_repo.insert_snapshot(payload)
		return {**payload, "diagnostics": diagnostics}

	def _daily_returns_from_cache(
		self,
		rows: List[Dict[str, Any]],
		market: str,
		snapshot_date: str,
		diagnostics: Dict[str, Any],
	) -> tuple:
		"""Compute equal-weighted daily returns from the ingestion cache.

		Returns (portfolio_daily_return, benchmark_daily_return).

		The benchmark return is None when the benchmark index is not present
		in the cache. Nothing is fetched externally and no price is invented:
		a holding without usable cached data is reported in
		`diagnostics["missing_prices"]` and contributes no return.
		"""
		yesterday = self._get_yesterday_trading_date(market)
		active_yesterday = self._get_active_symbols_as_of(market, yesterday)

		symbols = [
			(row.get("symbol") or "").upper() for row in rows
		]
		symbols = [s for s in symbols if s]

		# The benchmark must be requested as part of this same cache read.
		# Reading it from a cache built out of the holdings alone can never
		# return a benchmark row, so the benchmark NAV would stay frozen at its
		# baseline and the benchmark return would report 0.0 forever.
		# `get_quote_data_many` de-duplicates and batches, so adding one symbol
		# to an existing query does not add a request.
		benchmark_symbol = self.BENCHMARKS.get(market.upper())
		if benchmark_symbol:
			symbols.append(benchmark_symbol.upper())

		cache = {
			(str(row.get("symbol") or "")).upper(): row
			for row in self.stock_data_repo.get_quote_data_many(symbols)
		}

		holding_daily_returns: List[float] = []
		for row in rows:
			symbol = (row.get("symbol") or "").upper()
			if not symbol or symbol not in active_yesterday:
				continue

			cached = cache.get(symbol)
			price = self._cached_daily_return(
				cached, snapshot_date, diagnostics, symbol
			)
			if price is not None:
				holding_daily_returns.append(price)

		portfolio_daily_return = (
			sum(holding_daily_returns) / len(holding_daily_returns)
			if holding_daily_returns
			else 0.0
		)

		benchmark_daily_return = None

		if benchmark_symbol:
			benchmark_daily_return = self._cached_daily_return(
				cache.get(benchmark_symbol.upper()),
				snapshot_date,
				diagnostics,
				benchmark_symbol,
			)
			diagnostics["benchmark_status"] = (
				"cached" if benchmark_daily_return is not None else "missing"
			)
		else:
			diagnostics["benchmark_status"] = "unmapped"

		return portfolio_daily_return, benchmark_daily_return

	@staticmethod
	def _cached_daily_return(
		cached: Optional[Dict[str, Any]],
		snapshot_date: str,
		diagnostics: Dict[str, Any],
		label: str,
	) -> Optional[float]:
		"""Return the cached one-day return for `label`, or None.

		Uses only `stock_data.quote_json`, which the bounded ingestion job
		already populates. Returns None — never a fabricated number — when
		the row is missing, the price fields are unusable, or the cached row
		is not from the snapshot's trading day.
		"""
		if not cached:
			diagnostics["missing_prices"].append(label)
			return None

		if not _is_from_trading_day(cached.get("updated_at"), snapshot_date):
			diagnostics["stale_prices"].append(label)
			return None

		quote = cached.get("quote_json") or {}
		if isinstance(quote, str):
			try:
				quote = json.loads(quote)
			except (TypeError, ValueError):
				quote = {}

		current = _as_positive_float(quote.get("current_price"))
		previous = _as_positive_float(quote.get("previous_close"))

		if current is None or previous is None:
			diagnostics["missing_prices"].append(label)
			return None

		return (current - previous) / previous

	@staticmethod
	def _snapshot_date_of(snapshot: Dict[str, Any]) -> Optional[str]:
		"""Return the trading date recorded on a stored snapshot."""
		raw = snapshot.get("date") or snapshot.get("as_of")
		if raw is None:
			return None
		if isinstance(raw, _DATETIME_TYPE):
			return raw.date().isoformat()
		if isinstance(raw, _DATE_TYPE):
			return raw.isoformat()
		return str(raw).split("T", 1)[0]


	def snapshot_all(self) -> Dict[str, Any]:
		return {
			"India": self.snapshot_market("IN"),
			"USA": self.snapshot_market("US"),
		}

	def report_performance(self, market: str, period: str) -> Dict[str, Any]:
		market = (market or "").upper()
		period_key = (period or "").lower()
		benchmark_symbol = self.BENCHMARKS.get(market)
		now = datetime.utcnow()
		calendar = self._get_calendar(market)

		# Determine start and end dates based on period
		if period_key == "since-inception":
			# Use the first trading day on or after the reliable boundary
			boundary = self.RELIABLE_INCEPTION_START.get(market)
			if boundary is None:
				return self._not_available(market, period_key, benchmark_symbol, now,
					f"Unknown market {market}")
			date_start = calendar.next_trading_day(boundary - timedelta(days=1))
			if date_start is None:
				return self._not_available(market, period_key, benchmark_symbol, now,
					"No trading day found after reliable boundary")
			# For since-inception, date_end is None to include all available data
			# (matching the original behavior for backward compatibility)
			date_end = None
		elif period_key == "fy2025-26":
			date_start = calendar.next_trading_day(self.FY_START - timedelta(days=1))
			date_end = calendar.previous_trading_day(self.FY_END + timedelta(days=1))
		elif period_key in self.PERIOD_TRADING_DAYS:
			# Count back trading days from the most recent completed trading day
			today = now.date()
			latest_trading_day = calendar.previous_trading_day(today + timedelta(days=1))
			if latest_trading_day is None:
				return self._not_available(market, period_key, benchmark_symbol, now,
					"No recent trading day found")
			# Get N trading days back
			trading_days = calendar.trading_days(
				date(1900, 1, 1),  # far past
				latest_trading_day
			)
			required_days = self.PERIOD_TRADING_DAYS[period_key]
			if len(trading_days) < required_days:
				return self._not_available(market, period_key, benchmark_symbol, now,
					f"Insufficient trading history for {period_key} period")
			date_start = trading_days[-required_days]
			date_end = latest_trading_day
		else:
			return {
				"market": market,
				"period": period_key,
				"status": "error",
				"detail": f"Invalid period: {period_key}. Use 1m, 3m, 6m, 1y, since-inception, or fy2025-26.",
				"benchmark": None,
				"portfolio_return": None,
				"benchmark_return": None,
				"excess_return": None,
				"as_of": now.isoformat(),
				"holdings_count": 0,
			}

		columns = [
			"market", "as_of", "portfolio_nav", "benchmark_nav", "holdings_count",
		]
		snapshots = self.performance_repo.get_for_period(
			market, columns, date_start, date_end
		)

		if len(snapshots) < 2:
			reason = (
				"No alpha portfolio performance history covers this period"
				if not snapshots
				else "Insufficient historical performance data for this period"
			)
			return self._not_available(market, period_key, benchmark_symbol, now, reason)

		start = snapshots[0]
		end = snapshots[-1]

		portfolio_nav_start = start.get("portfolio_nav")
		portfolio_nav_end = end.get("portfolio_nav")
		benchmark_nav_start = start.get("benchmark_nav")
		benchmark_nav_end = end.get("benchmark_nav")

		if portfolio_nav_start is None or portfolio_nav_end is None:
			return self._not_available(
				market, period_key, benchmark_symbol, now,
				"Insufficient portfolio return data",
			)

		if benchmark_nav_start is None or benchmark_nav_end is None:
			return self._not_available(
				market, period_key, benchmark_symbol, now,
				"Insufficient benchmark return data",
			)

		try:
			portfolio_return = (
				float(portfolio_nav_end) / float(portfolio_nav_start)
			) - 1.0
		except (TypeError, ValueError, ZeroDivisionError):
			return self._not_available(
				market, period_key, benchmark_symbol, now,
				"Invalid portfolio return data",
			)

		try:
			benchmark_return = (
				float(benchmark_nav_end) / float(benchmark_nav_start)
			) - 1.0
		except (TypeError, ValueError, ZeroDivisionError):
			return self._not_available(
				market, period_key, benchmark_symbol, now,
				"Invalid benchmark return data",
			)

		excess_return = portfolio_return - benchmark_return

		return {
			"market": market,
			"period": period_key,
			"status": "available",
			"benchmark": benchmark_symbol,
			"portfolio_return": portfolio_return,
			"benchmark_return": benchmark_return,
			"excess_return": excess_return,
			"as_of": end.get("as_of"),
			"holdings_count": end.get("holdings_count", 0),
			"date_start": date_start.isoformat(),
			"date_end": date_end.isoformat() if date_end else None,
		}

	def _get_yesterday_trading_date(self, market: str) -> date:
		now = datetime.utcnow()
		try:
			calendar = self._get_calendar(market)
			return calendar.previous_trading_day(now.date()) or (now - timedelta(days=1)).date()
		except ValueError:
			# Unsupported market - fall back to simple weekday check
			for offset in range(1, 8):
				candidate = (now - timedelta(days=offset)).date()
				if candidate.weekday() < 5:
					return candidate
			return (now - timedelta(days=1)).date()

	def _get_active_symbols_as_of(
		self, market: str, as_of: date
	) -> Set[str]:
		as_of_str = as_of.isoformat()
		events = self.alpha_history_repo.get_events_up_to(market, as_of_str)

		active: Set[str] = set()
		for event in events:
			symbol = event.get("symbol")
			if not symbol:
				continue
			action = event.get("action")
			if action == "ADD":
				active.add(symbol)
			elif action == "REMOVE":
				active.discard(symbol)

		return active

	def _not_available(
		self,
		market: str,
		period: str,
		benchmark: Optional[str],
		now: datetime,
		reason: str = "No alpha portfolio performance history available for this period",
	) -> Dict[str, Any]:
		return {
			"market": market,
			"period": period,
			"status": "not_available",
			"benchmark": benchmark,
			"portfolio_return": None,
			"benchmark_return": None,
			"excess_return": None,
			"as_of": now.isoformat(),
			"holdings_count": 0,
			"reason": reason,
		}