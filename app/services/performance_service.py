from datetime import datetime
from typing import Any, Dict, List, Optional

from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.repositories.portfolio_performance_repository import (
	PortfolioPerformanceRepository,
)
from app.services.market_data_service import MarketDataService


class PerformanceService:
	"""
	Computes daily portfolio performance snapshots and stores them
	in the `portfolio_performance` table.

	Approach:
	- For each holding in `alpha_portfolio` calculate individual return since `entry_price`.
	- For the same holding period compute benchmark return using index historical prices.
	- Portfolio return is the arithmetic mean of individual holding returns.
	- Benchmark return is the arithmetic mean of corresponding benchmark returns.
	- Alpha = portfolio_return - benchmark_return
	"""

	BENCHMARKS = {
		"IN": "^NSEI",   # NIFTY 50
		"US": "^IXIC",   # NASDAQ Composite
	}

	def __init__(self):
		self.alpha_repo = AlphaPortfolioRepository()
		self.performance_repo = PortfolioPerformanceRepository()
		self.market = MarketDataService()

	def snapshot_market(self, market: str) -> Optional[Dict[str, Any]]:
		rows = self.alpha_repo.get_all(market)

		if not rows:
			snapshot = {
				"market": market,
				"as_of": datetime.utcnow().isoformat(),
				"portfolio_return": 0.0,
				"benchmark_return": 0.0,
				"alpha": 0.0,
				"holdings_count": 0,
			}

			self.performance_repo.insert_daily_snapshot(market, snapshot)
			return snapshot

		holding_returns: List[float] = []
		benchmark_returns: List[float] = []

		benchmark_symbol = self.BENCHMARKS.get(market.upper())

		# fetch current benchmark price once
		bench_current = None
		try:
			if benchmark_symbol:
				bench_q = self.market.yahoo.get_quote(benchmark_symbol)
				bench_current = bench_q.current_price
		except Exception:
			bench_current = None

		for row in rows:
			symbol = (row.get("symbol") or "").upper()
			entry_price = row.get("entry_price")
			entry_date_raw = row.get("entry_date")

			if not symbol or not entry_price:
				continue

			try:
				current_payload = self.market.get_stock(symbol)
				quote = current_payload.get("quote_json")
				current_price = quote.get("current_price") if quote else None
			except Exception:
				current_price = None

			if not current_price or not entry_price:
				continue

			try:
				h_return = (current_price - float(entry_price)) / float(entry_price)
			except Exception:
				continue

			holding_returns.append(h_return)

			# compute benchmark return for the same period
			bench_return_for_holding = None

			if benchmark_symbol and bench_current is not None and entry_date_raw:
				try:
					# parse entry date
					entry_date = datetime.fromisoformat(entry_date_raw.replace("Z", "+00:00")).date()

					history = self.market.yahoo.get_historical_prices(
						benchmark_symbol, period="max", interval="1d"
					)

					# find last available price on or before entry_date
					bench_entry_price = None
					for item in reversed(history.prices):
						if item.date <= entry_date:
							bench_entry_price = item.close
							break

					if bench_entry_price and bench_entry_price > 0:
						bench_return_for_holding = (bench_current - bench_entry_price) / bench_entry_price
				except Exception:
					bench_return_for_holding = None

			if bench_return_for_holding is not None:
				benchmark_returns.append(bench_return_for_holding)

		# aggregate
		portfolio_return = float(sum(holding_returns) / len(holding_returns)) if holding_returns else 0.0
		benchmark_return = float(sum(benchmark_returns) / len(benchmark_returns)) if benchmark_returns else 0.0
		alpha = portfolio_return - benchmark_return

		snapshot = {
			"market": market,
			"as_of": datetime.utcnow().isoformat(),
			"portfolio_return": portfolio_return,
			"benchmark_return": benchmark_return,
			"alpha": alpha,
			"holdings_count": len(holding_returns),
		}

		self.performance_repo.insert_daily_snapshot(market, snapshot)

		return snapshot

	def snapshot_all(self) -> Dict[str, Any]:
		return {
			"India": self.snapshot_market("IN"),
			"USA": self.snapshot_market("US"),
		}

