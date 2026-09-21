from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from app.repositories.alpha_history_repository import AlphaHistoryRepository
from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.repositories.portfolio_performance_repository import (
	PortfolioPerformanceRepository,
)
from app.services.market_data_service import MarketDataService


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
	"""

	BENCHMARKS = {
		"IN": "^NSEI",
		"US": "^IXIC",
	}

	PERIOD_DAYS = {
		"1m": 30,
		"6m": 180,
		"1y": 365,
	}

	FY_START = date(2025, 4, 1)
	FY_END = date(2026, 3, 31)

	def __init__(self):
		self.alpha_repo = AlphaPortfolioRepository()
		self.alpha_history_repo = AlphaHistoryRepository()
		self.performance_repo = PortfolioPerformanceRepository()
		self.market = MarketDataService()

	def snapshot_market(self, market: str) -> Optional[Dict[str, Any]]:
		rows = self.alpha_repo.get_all(market)

		if not rows:
			last = self.performance_repo.get_latest(market)
			if last is None:
				portfolio_nav = 100.0
				benchmark_nav = 100.0
			else:
				portfolio_nav = last.get("portfolio_nav", 100.0)
				benchmark_nav = last.get("benchmark_nav", 100.0)
			snapshot = {
				"market": market,
				"as_of": datetime.utcnow().isoformat(),
				"portfolio_nav": portfolio_nav,
				"benchmark_nav": benchmark_nav,
				"holdings_count": 0,
			}
			self.performance_repo.insert_snapshot(snapshot)
			return snapshot

		last = self.performance_repo.get_latest(market)

		if last is None:
			portfolio_nav = 100.0
			benchmark_nav = 100.0
		else:
			prev_nav = last.get("portfolio_nav", 100.0)
			prev_bench_nav = last.get("benchmark_nav", 100.0)

			yesterday = self._get_yesterday_trading_date()

			active_yesterday = self._get_active_symbols_as_of(
				market, yesterday
			)

			holding_daily_returns: List[float] = []
			for row in rows:
				symbol = (row.get("symbol") or "").upper()
				if symbol not in active_yesterday:
					continue
				try:
					quote = self.market.yahoo.get_quote(symbol)
					current_price = quote.current_price
					yesterday_price = self._fetch_price_for_date(
						symbol, -1
					)
					if current_price and yesterday_price and yesterday_price > 0:
						holding_daily_returns.append(
							(float(current_price) - float(yesterday_price))
							/ float(yesterday_price)
						)
				except Exception:
					continue

			benchmark_daily_returns: List[float] = []
			benchmark_symbol = self.BENCHMARKS.get(market.upper())
			if benchmark_symbol:
				try:
					bench_quote = self.market.yahoo.get_quote(
						benchmark_symbol
					)
					bench_current = bench_quote.current_price
					bench_yesterday = self._fetch_price_for_date(
						benchmark_symbol, -1
					)
					if bench_current and bench_yesterday and bench_yesterday > 0:
						benchmark_daily_returns.append(
							(float(bench_current) - float(bench_yesterday))
							/ float(bench_yesterday)
						)
				except Exception:
					pass

			portfolio_daily_return = (
				sum(holding_daily_returns) / len(holding_daily_returns)
				if holding_daily_returns
				else 0.0
			)
			benchmark_daily_return = (
				sum(benchmark_daily_returns) / len(benchmark_daily_returns)
				if benchmark_daily_returns
				else 0.0
			)

			portfolio_nav = prev_nav * (1 + portfolio_daily_return)
			benchmark_nav = prev_bench_nav * (1 + benchmark_daily_return)

		snapshot = {
			"market": market,
			"as_of": datetime.utcnow().isoformat(),
			"portfolio_nav": portfolio_nav,
			"benchmark_nav": benchmark_nav,
			"holdings_count": len(rows),
		}

		self.performance_repo.insert_snapshot(snapshot)
		return snapshot

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

		if period_key == "since-inception":
			date_start = None
			date_end = None
		elif period_key == "fy2025-26":
			date_start = self.FY_START
			date_end = self.FY_END
		elif period_key in self.PERIOD_DAYS:
			date_start = now.date() - timedelta(days=self.PERIOD_DAYS[period_key])
			date_end = now.date()
		else:
			return {
				"market": market,
				"period": period_key,
				"status": "error",
				"detail": f"Invalid period: {period_key}. Use 1m, 6m, 1y, since-inception, or fy2025-26.",
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
			benchmark_return = None

		excess_return = (
			portfolio_return - benchmark_return
			if benchmark_return is not None
			else None
		)

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
		}

	def _get_yesterday_trading_date(self) -> date:
		now = datetime.utcnow()
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

	def _fetch_price_for_date(
		self, symbol: str, days_offset: int
	) -> Optional[float]:
		try:
			target_date = (
				datetime.utcnow() + timedelta(days=days_offset)
			).date()
			history = self.market.yahoo.get_historical_prices(
				symbol, period="1mo", interval="1d"
			)
			return self._price_on_exact_date(history.prices, target_date)
		except Exception:
			return None

	@staticmethod
	def _price_on_exact_date(
		prices: List[Any], target: date
	) -> Optional[float]:
		for item in prices:
			d = getattr(item, "date", None)
			if d is None:
				continue
			if hasattr(d, "date"):
				d = d.date()
			if d == target:
				close = getattr(item, "close", None)
				if close is not None:
					return float(close)
		return None

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