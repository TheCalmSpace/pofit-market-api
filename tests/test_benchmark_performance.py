"""Phase 1 - live benchmark calculation for Alpha Performance.

Covers the two defects found in the read-only audit:

1. `^NSEI` / `^IXIC` were absent from the ingestion cache, and no existing code
   path could create them.
2. `PerformanceService` built its quote cache out of the alpha holdings only
   and then looked the benchmark up in that cache, so the benchmark lookup
   could never succeed. `benchmark_nav` stayed pinned at its 100.0 baseline
   and `benchmark_return` was reported as 0.0 forever.

The regression tests below deliberately use a cache double that filters by the
requested symbols, the way the real PostgREST query does. Mocking the
repository to return rows regardless of what was asked for is what let defect 2
reach production with a green test suite.
"""

import inspect
import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.portfolio_performance_repository import (
	PortfolioPerformanceRepository,
)
from app.scheduler import (
	generate_india,
	generate_usa,
	_refresh_benchmark_quote,
)
from app.services.market_data_service import (
	BENCHMARK_CACHE_STATUS,
	MarketDataService,
)
from app.services.performance_service import PerformanceService
from app.services.yahoo_service import SymbolNotFoundError
from app.utils.symbol import (
	BENCHMARK_SYMBOLS,
	MARKET_BENCHMARKS,
)


def cache_row(symbol, current_price, previous_close, updated_at=None):
	"""Build a `stock_data` row as the ingestion cache stores it."""
	return {
		"symbol": symbol,
		"quote_json": {
			"current_price": current_price,
			"previous_close": previous_close,
		},
		"updated_at": updated_at or datetime.utcnow().isoformat(),
	}


class FilteringQuoteCache:
	"""Stands in for `StockDataRepository.get_quote_data_many`.

	Returns only the rows whose symbol was actually requested, which is what the
	batched `or=(symbol.eq.X,...)` PostgREST query does. A mock that returns
	everything regardless of the request would hide the defect under test.
	"""

	def __init__(self, rows):
		self.rows = {r["symbol"].upper(): r for r in rows}
		self.requests = []

	def get_quote_data_many(self, symbols):
		requested = [str(s or "").upper() for s in symbols]
		self.requests.append(list(requested))
		return [self.rows[s] for s in requested if s in self.rows]


@pytest.fixture
def mock_alpha_repo():
	return MagicMock()


@pytest.fixture
def mock_alpha_history_repo():
	return MagicMock()


@pytest.fixture
def mock_perf_repo():
	return MagicMock(spec=PortfolioPerformanceRepository)


@pytest.fixture
def performance_service(
	mock_alpha_repo,
	mock_alpha_history_repo,
	mock_perf_repo,
):
	"""A PerformanceService with every repository mocked out."""
	with patch(
		"app.services.performance_service.AlphaPortfolioRepository",
		return_value=mock_alpha_repo,
	), patch(
		"app.services.performance_service.AlphaHistoryRepository",
		return_value=mock_alpha_history_repo,
	), patch(
		"app.services.performance_service.PortfolioPerformanceRepository",
		return_value=mock_perf_repo,
	), patch(
		"app.services.performance_service.StockDataRepository",
		return_value=MagicMock(),
	):
		service = PerformanceService()
		service.alpha_repo = mock_alpha_repo
		service.alpha_history_repo = mock_alpha_history_repo
		service.performance_repo = mock_perf_repo
		return service


def arm_one_day_step(
	performance_service,
	mock_alpha_repo,
	mock_alpha_history_repo,
	mock_perf_repo,
	cache,
	holding_symbol="ABC",
):
	"""Put the service on its second snapshot so a daily return is computed."""
	mock_alpha_repo.get_all.return_value = [{"symbol": holding_symbol}]
	mock_alpha_history_repo.get_events_up_to.return_value = [
		{"symbol": holding_symbol, "action": "ADD"}
	]
	mock_perf_repo.get_latest.return_value = {
		"date": "2020-01-01",
		"portfolio_nav": 100.0,
		"benchmark_nav": 100.0,
	}
	performance_service.stock_data_repo = cache


class TestBenchmarkSymbolSelection:
	"""Requirement 1: IN -> ^NSEI, US -> ^IXIC."""

	def test_market_benchmark_mapping(self):
		assert MARKET_BENCHMARKS["IN"] == "^NSEI"
		assert MARKET_BENCHMARKS["US"] == "^IXIC"

	def test_performance_service_uses_the_shared_mapping(self):
		# One source of truth, so the refresh and the calculation cannot drift.
		assert PerformanceService.BENCHMARKS is MARKET_BENCHMARKS
		assert PerformanceService.BENCHMARKS["IN"] == "^NSEI"
		assert PerformanceService.BENCHMARKS["US"] == "^IXIC"

	def test_accepted_benchmark_symbols(self):
		assert BENCHMARK_SYMBOLS == frozenset({"^NSEI", "^IXIC"})

	@pytest.mark.parametrize(
		"market,expected",
		[("IN", "^NSEI"), ("US", "^IXIC"), ("in", "^NSEI"), ("us", "^IXIC")],
	)
	def test_market_resolves_expected_benchmark(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		market,
		expected,
	):
		cache = FilteringQuoteCache([])
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
		)

		performance_service.snapshot_market(market)

		assert expected in cache.requests[0]

	def test_unmapped_market_adds_no_symbol_and_reports_unmapped(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache([])
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
		)

		result = performance_service.snapshot_market("ZZ")

		assert cache.requests[0] == ["ABC"]
		assert result["diagnostics"]["benchmark_status"] == "unmapped"


class TestBenchmarkRequestedInSameQuery:
	"""Requirement 2: holdings and benchmark come from ONE batched DB read."""

	def test_benchmark_is_included_in_the_single_cache_request(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache([])
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		performance_service.snapshot_market("IN")

		# Exactly one request, carrying the holding and the benchmark together.
		assert len(cache.requests) == 1
		requested = cache.requests[0]
		assert "RELIANCE" in requested
		assert "^NSEI" in requested

	def test_us_snapshot_requests_ixic(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache([])
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="AAPL",
		)

		performance_service.snapshot_market("US")

		requested = cache.requests[0]
		assert "AAPL" in requested
		assert "^IXIC" in requested

	def test_duplicate_benchmark_symbol_is_not_requested_twice(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		"""The repository de-duplicates, so a repeat cannot double-count."""
		cache = FilteringQuoteCache([])
		mock_alpha_repo.get_all.return_value = [{"symbol": "RELIANCE"}]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "RELIANCE", "action": "ADD"}
		]
		mock_perf_repo.get_latest.return_value = {
			"date": "2020-01-01",
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		performance_service.stock_data_repo = cache

		performance_service.snapshot_market("IN")

		# get_quote_data_many normalises and de-duplicates before querying.
		from app.utils.postgrest import normalized_unique_symbols

		assert normalized_unique_symbols(
			cache.requests[0]
		) == normalized_unique_symbols(cache.requests[0])
		assert cache.requests[0].count("^NSEI") == 1


class TestBenchmarkReadFromCache:
	"""Requirement 3: the cached benchmark actually moves the benchmark NAV."""

	def test_cached_benchmark_advances_benchmark_nav(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache(
			[
				cache_row("RELIANCE", 110.0, 100.0),
				cache_row("^NSEI", 105.0, 100.0),
			]
		)
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert result["benchmark_nav"] == pytest.approx(105.0)
		assert result["diagnostics"]["benchmark_status"] == "cached"

	def test_benchmark_daily_return_formula_is_unchanged(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		"""benchmark_nav = prev * (1 + (current - previous) / previous)."""
		cache = FilteringQuoteCache(
			[
				cache_row("RELIANCE", 100.0, 100.0),
				cache_row("^NSEI", 22555.75, 22421.95),
			]
		)
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		result = performance_service.snapshot_market("IN")

		expected = 100.0 * (1 + (22555.75 - 22421.95) / 22421.95)
		assert result["benchmark_nav"] == pytest.approx(expected)

	def test_benchmark_is_not_treated_as_a_holding(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		"""The index must not leak into the equal-weighted portfolio mean."""
		cache = FilteringQuoteCache(
			[
				cache_row("RELIANCE", 100.0, 100.0),
				cache_row("^NSEI", 50.0, 100.0),
			]
		)
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		result = performance_service.snapshot_market("IN")

		# Portfolio is flat; only the benchmark moved.
		assert result["portfolio_nav"] == pytest.approx(100.0)
		assert result["benchmark_nav"] == pytest.approx(50.0)

	def test_alpha_is_portfolio_minus_benchmark(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		mock_perf_repo.get_for_period.return_value = [
			{
				"as_of": "2026-01-01T00:00:00",
				"portfolio_nav": 100.0,
				"benchmark_nav": 100.0,
				"holdings_count": 1,
			},
			{
				"as_of": "2026-06-01T00:00:00",
				"portfolio_nav": 110.0,
				"benchmark_nav": 95.0,
				"holdings_count": 1,
			},
		]

		report = performance_service.report_performance("IN", "1m")

		assert report["portfolio_return"] == pytest.approx(0.10)
		assert report["benchmark_return"] == pytest.approx(-0.05)
		assert report["excess_return"] == pytest.approx(0.15)


class TestMissingBenchmarkStaysLocal:
	"""Requirement 4: a missing benchmark never triggers an external call."""

	def test_missing_benchmark_carries_nav_and_reports_missing(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache([cache_row("RELIANCE", 110.0, 100.0)])
		mock_alpha_repo.get_all.return_value = [{"symbol": "RELIANCE"}]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "RELIANCE", "action": "ADD"}
		]
		mock_perf_repo.get_latest.return_value = {
			"date": "2020-01-01",
			"portfolio_nav": 100.0,
			"benchmark_nav": 137.0,
		}
		performance_service.stock_data_repo = cache

		result = performance_service.snapshot_market("IN")

		assert result["benchmark_nav"] == 137.0
		assert result["diagnostics"]["benchmark_status"] == "missing"
		# The benchmark was still asked for; it simply had no row.
		assert "^NSEI" in cache.requests[0]

	def test_stale_benchmark_is_rejected_rather_than_substituted(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		"""A row from an earlier day describes a different session."""
		stale = cache_row(
			"^NSEI", 105.0, 100.0, updated_at="2020-01-01T00:00:00"
		)
		cache = FilteringQuoteCache(
			[cache_row("RELIANCE", 110.0, 100.0), stale]
		)
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		result = performance_service.snapshot_market("IN")

		assert result["benchmark_nav"] == pytest.approx(100.0)
		assert result["diagnostics"]["benchmark_status"] == "missing"
		assert "^NSEI" in result["diagnostics"]["stale_prices"]

	def test_unusable_benchmark_prices_are_rejected(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache(
			[
				cache_row("RELIANCE", 110.0, 100.0),
				cache_row("^NSEI", 0, 100.0),
			]
		)
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		result = performance_service.snapshot_market("IN")

		assert result["benchmark_nav"] == pytest.approx(100.0)
		assert result["diagnostics"]["benchmark_status"] == "missing"


class TestBenchmarkQuoteRefresh:
	"""Requirement 5: the refresh stores exactly the quote fields needed."""

	@pytest.fixture
	def market_service(self):
		with patch(
			"app.services.market_data_service.StockDataRepository"
		) as repo, patch(
			"app.services.market_data_service.StockRepository"
		) as stock_repo, patch(
			"app.services.market_data_service.YahooService"
		) as yahoo, patch(
			"app.services.market_data_service.MetricsService"
		) as metrics, patch(
			"app.services.market_data_service.ScoreService"
		) as score, patch(
			"app.services.market_data_service.ExplanationService"
		) as explanation, patch(
			"app.services.market_data_service.EligibilityService"
		) as eligibility:
			service = MarketDataService()
			service.stock_data_repo = repo
			service.stock_repo = stock_repo
			service.yahoo = yahoo
			service.metrics = metrics.return_value
			service.score = score.return_value
			service.explanation = explanation.return_value
			service.eligibility = eligibility.return_value
			return service

	@pytest.mark.parametrize("symbol", ["^NSEI", "^IXIC"])
	def test_stores_current_price_previous_close_and_updated_at(
		self, market_service, symbol
	):
		quote = MagicMock()
		market_service.yahoo.get_quote.return_value = quote
		market_service._stored_payload = MagicMock(return_value={})

		with patch(
			"app.services.market_data_service.to_dict",
			return_value={"current_price": 22555.75, "previous_close": 22421.95},
		):
			market_service.refresh_benchmark_quote(symbol)

		saved_symbol, payload = (
			market_service.stock_data_repo.save.call_args[0]
		)

		assert saved_symbol == symbol
		assert payload["quote_json"]["current_price"] == 22555.75
		assert payload["quote_json"]["previous_close"] == 22421.95
		assert payload["updated_at"]
		# The freshness marker the calculation reads must be present.
		assert payload["cache_status"] == BENCHMARK_CACHE_STATUS

	def test_makes_exactly_one_quote_call(self, market_service):
		market_service._stored_payload = MagicMock(return_value={})
		with patch(
			"app.services.market_data_service.to_dict", return_value={}
		):
			market_service.refresh_benchmark_quote("^NSEI")

		assert market_service.yahoo.get_quote.call_count == 1
		market_service.yahoo.get_quote.assert_called_once_with("^NSEI")

	@pytest.mark.parametrize("forbidden", [
		"get_financials",
		"get_financial_history",
		"get_historical_prices",
		"build_score",
		"build_metrics",
		"build_explanation",
		"build_eligibility",
		"refresh_stock",
	])
	def test_never_fetches_fundamentals_history_or_score(
		self, market_service, forbidden
	):
		"""No history download, no scoring, no full refresh for an index."""
		market_service._stored_payload = MagicMock(return_value={})
		with patch(
			"app.services.market_data_service.to_dict", return_value={}
		):
			market_service.refresh_benchmark_quote("^NSEI")

		# Runtime: none of these collaborators were touched.
		for attribute in (
			"get_financials",
			"get_financial_history",
			"get_historical_prices",
		):
			assert not getattr(
				market_service.yahoo, attribute
			).called, attribute

		assert not market_service.metrics.build_metrics.called
		assert not market_service.score.build_score.called
		assert not market_service.explanation.build_explanation.called
		assert not market_service.eligibility.build.called

		# Source: the forbidden call is not even referenced, so a future
		# edit cannot reintroduce it behind a runtime branch. Matched as a
		# call, not as a bare name, because the docstring explains in prose
		# why each of these is avoided.
		source = inspect.getsource(MarketDataService.refresh_benchmark_quote)
		assert not re.search(r"\.\s*%s\s*\(" % re.escape(forbidden), source), (
			forbidden
		)

	def test_stores_no_fabricated_fundamentals(self, market_service):
		"""An index has no fundamentals, so none may be written."""
		market_service._stored_payload = MagicMock(return_value={})
		with patch(
			"app.services.market_data_service.to_dict", return_value={}
		):
			market_service.refresh_benchmark_quote("^NSEI")

		_, payload = market_service.stock_data_repo.save.call_args[0]

		for column in (
			"eligibility_json",
			"metrics_json",
			"score_json",
			"explanation_json",
			"financials_json",
			"financial_history_json",
			"income_json",
			"balance_json",
			"cashflow_json",
			"ratios_json",
			"profile_json",
		):
			assert column not in payload, column

	def test_does_not_read_or_write_the_stocks_table(self, market_service):
		"""Requirement 6: a benchmark is never enrolled in the universe."""
		market_service._stored_payload = MagicMock(return_value={})
		with patch(
			"app.services.market_data_service.to_dict", return_value={}
		):
			market_service.refresh_benchmark_quote("^NSEI")

		# The only repository write is the quote cache.
		assert market_service.stock_data_repo.save.call_count == 1
		assert not market_service.stock_repo.save.called
		for method in (
			"get_by_symbol",
			"update_metadata",
			"list_all_by_country",
			"list_all_active",
		):
			assert not getattr(
				market_service.stock_repo, method
			).called, method

	def test_source_never_mentions_the_stocks_table(self):
		source = inspect.getsource(MarketDataService.refresh_benchmark_quote)
		for forbidden in (
			"stock_repo",
			"list_all_by_country",
			"update_metadata",
			'"stocks"',
		):
			assert forbidden not in source, forbidden

	@pytest.mark.parametrize(
		"symbol", ["RELIANCE", "AAPL", "MONIFTY500", "", "^SPX", "NIFTY50"]
	)
	def test_rejects_any_symbol_that_is_not_a_benchmark(
		self, market_service, symbol
	):
		"""The path cannot be repurposed to write a partial row for a stock."""
		market_service._stored_payload = MagicMock(return_value={})

		with pytest.raises(SymbolNotFoundError):
			market_service.refresh_benchmark_quote(symbol)

		assert not market_service.stock_data_repo.save.called
		assert not market_service.yahoo.get_quote.called

	def test_benchmark_symbol_is_normalised(self, market_service):
		market_service._stored_payload = MagicMock(return_value={})
		with patch(
			"app.services.market_data_service.to_dict", return_value={}
		):
			market_service.refresh_benchmark_quote("  ^nsei  ")

		market_service.yahoo.get_quote.assert_called_once_with("^NSEI")
		assert (
			market_service.stock_data_repo.save.call_args[0][0] == "^NSEI"
		)


class TestSchedulerAutomation:
	"""Requirements 7 and 8: one refresh per market, right before the snapshot."""

	@pytest.fixture
	def wired(self):
		"""Patch every collaborator of the two market generation functions."""
		order = []

		with patch("app.scheduler.DailyTopPicksService") as daily, patch(
			"app.scheduler.AlphaPortfolioService"
		) as alpha, patch(
			"app.scheduler.MarketDataService"
		) as market, patch(
			"app.scheduler.PerformanceService"
		) as perf:
			daily.return_value.generate_country.return_value = ["p1"]
			alpha.return_value.reconcile_market.return_value = 3
			market.return_value.refresh_benchmark_quote.side_effect = (
				lambda symbol: order.append(("benchmark", symbol))
			)
			perf.return_value.snapshot_market.side_effect = (
				lambda market_code: order.append(("snapshot", market_code))
			)
			yield {
				"daily": daily,
				"alpha": alpha,
				"market": market,
				"perf": perf,
				"order": order,
			}

	@pytest.mark.parametrize(
		"job,market_code,benchmark",
		[
			(generate_india, "IN", "^NSEI"),
			(generate_usa, "US", "^IXIC"),
		],
	)
	def test_each_market_refreshes_its_benchmark_once(
		self, wired, job, market_code, benchmark
	):
		job()

		refresh = wired["market"].return_value.refresh_benchmark_quote
		assert refresh.call_count == 1
		refresh.assert_called_once_with(benchmark)
		wired["perf"].return_value.snapshot_market.assert_called_once_with(
			market_code
		)

	@pytest.mark.parametrize(
		"job,market_code,benchmark",
		[
			(generate_india, "IN", "^NSEI"),
			(generate_usa, "US", "^IXIC"),
		],
	)
	def test_benchmark_refresh_precedes_the_snapshot_exactly_once(
		self, wired, job, market_code, benchmark
	):
		job()

		assert wired["order"] == [
			("benchmark", benchmark),
			("snapshot", market_code),
		]

	def test_benchmark_failure_does_not_abort_the_snapshot(self, wired):
		wired["market"].return_value.refresh_benchmark_quote.side_effect = (
			Exception("yahoo unavailable")
		)

		generate_india()

		# The snapshot still happens, so the NAV series keeps stepping.
		wired["perf"].return_value.snapshot_market.assert_called_once_with("IN")

	def test_two_market_runs_use_two_different_benchmarks(self, wired):
		generate_india()
		generate_usa()

		refresh = wired["market"].return_value.refresh_benchmark_quote
		assert refresh.call_count == 2
		assert [c.args[0] for c in refresh.call_args_list] == [
			"^NSEI",
			"^IXIC",
		]

	def test_unmapped_market_makes_no_refresh(self, wired):
		_refresh_benchmark_quote("ZZ")

		assert not wired["market"].return_value.refresh_benchmark_quote.called


class TestNoProviderDependency:
	"""Requirement 9: PerformanceService stays database-cache-only."""

	def test_service_holds_no_provider(self, performance_service):
		assert not hasattr(performance_service, "market")
		assert not hasattr(performance_service, "yahoo")
		assert not hasattr(performance_service, "yahoo_service")
		assert hasattr(performance_service, "stock_data_repo")

	def test_source_has_no_external_provider_reference(self):
		source = inspect.getsource(
			inspect.getmodule(PerformanceService)
		)
		for forbidden in (
			"self.market",
			"get_quote(",
			"get_historical_prices",
			"get_financials",
			"yahoo_service",
			"YahooService",
			"MarketDataService",
			"YahooProvider",
			"import requests",
			"urllib",
			"http",
		):
			assert forbidden not in source, forbidden

	def test_snapshot_makes_no_external_call_even_with_provider_attached(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache(
			[
				cache_row("RELIANCE", 110.0, 100.0),
				cache_row("^NSEI", 105.0, 100.0),
			]
		)
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		class ExplodingProvider:
			def __getattr__(self, name):
				raise AssertionError(
					"external market-data call attempted: %s" % name
				)

		performance_service.market = ExplodingProvider()
		try:
			result = performance_service.snapshot_market("IN")
		finally:
			del performance_service.market

		# One batched DB read, one snapshot write, benchmark still resolved.
		assert len(cache.requests) == 1
		assert mock_perf_repo.insert_snapshot.call_count == 1
		assert result["benchmark_nav"] == pytest.approx(105.0)

	def test_missing_benchmark_makes_no_external_call(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
	):
		cache = FilteringQuoteCache([cache_row("RELIANCE", 110.0, 100.0)])
		arm_one_day_step(
			performance_service,
			mock_alpha_repo,
			mock_alpha_history_repo,
			mock_perf_repo,
			cache,
			holding_symbol="RELIANCE",
		)

		class ExplodingProvider:
			def __getattr__(self, name):
				raise AssertionError(
					"external market-data call attempted: %s" % name
				)

		performance_service.market = ExplodingProvider()
		try:
			result = performance_service.snapshot_market("IN")
		finally:
			del performance_service.market

		assert result["diagnostics"]["benchmark_status"] == "missing"
		assert result["benchmark_nav"] == pytest.approx(100.0)


class TestBenchmarkEgressBound:
	"""The benchmark refresh must stay one quote call per market per day."""

	def test_refresh_issues_exactly_one_provider_call(self):
		"""Guards against a future change widening the benchmark's egress."""
		source = inspect.getsource(MarketDataService.refresh_benchmark_quote)
		assert source.count("self.yahoo.get_quote(") == 1
		# No loop over a universe, and no historical series request.
		for forbidden in (
			"get_historical_prices",
			"get_financial_history",
			"for symbol in",
		):
			assert forbidden not in source, forbidden

	def test_scheduler_calls_refresh_once_per_generation(self):
		source = inspect.getsource(
			__import__("app.scheduler", fromlist=["x"])
		)
		# One helper, invoked once per market job, and never from the
		# 4-hourly ingestion job.
		assert source.count("_refresh_benchmark_quote(") == 3  # def + IN + US
		assert "ingest_stock_data" in source
		# The ingestion job must not reference the benchmark at all.
		ingestion = source.split("def ingest_stock_data(")[1].split("\ndef ")[0]
		assert "_refresh_benchmark_quote" not in ingestion
