import pytest
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, patch

from app.models import AlphaPerformanceSummaryResponse
from app.services.performance_service import PerformanceService
from app.repositories.portfolio_performance_repository import (
	PortfolioPerformanceRepository,
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
def mock_stock_data_repo():
	return MagicMock()


@pytest.fixture
def performance_service(
	mock_alpha_repo,
	mock_alpha_history_repo,
	mock_perf_repo,
	mock_stock_data_repo,
):
	with patch(
		"app.services.performance_service.AlphaPortfolioRepository",
		return_value=mock_alpha_repo,
	):
		with patch(
			"app.services.performance_service.AlphaHistoryRepository",
			return_value=mock_alpha_history_repo,
		):
			with patch(
				"app.services.performance_service.PortfolioPerformanceRepository",
				return_value=mock_perf_repo,
			):
				with patch(
					"app.services.performance_service.StockDataRepository",
					return_value=mock_stock_data_repo,
				):
					service = PerformanceService()
					service.alpha_repo = mock_alpha_repo
					service.alpha_history_repo = mock_alpha_history_repo
					service.performance_repo = mock_perf_repo
					service.stock_data_repo = mock_stock_data_repo
					return service


class TestSnapshotPayload:
	"""A: every NOT NULL column must be present in the INSERT payload."""

	def test_payload_supplies_every_required_column(
		self, performance_service, mock_alpha_repo, mock_perf_repo
	):
		mock_alpha_repo.get_all.return_value = []
		mock_perf_repo.get_latest.return_value = None

		result = performance_service.snapshot_market("IN")

		payload = mock_perf_repo.insert_snapshot.call_args[0][0]
		for field in (
			"market",
			"date",
			"as_of",
			"period",
			"portfolio_nav",
			"benchmark_nav",
			"holdings_count",
		):
			assert field in payload, "snapshot payload is missing %s" % field
			assert payload[field] is not None

		assert payload["period"] == "INCEPTION"
		assert result["date"] == payload["date"]

	def test_date_is_derived_from_as_of(
		self, performance_service, mock_alpha_repo, mock_perf_repo
	):
		mock_alpha_repo.get_all.return_value = []
		mock_perf_repo.get_latest.return_value = None

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 3, 4, 9, 30, 0)
			performance_service.snapshot_market("IN")

		payload = mock_perf_repo.insert_snapshot.call_args[0][0]
		assert payload["date"] == "2026-03-04"
		assert payload["as_of"].startswith("2026-03-04T09:30:00")

	def test_payload_contains_no_non_schema_keys(
		self, performance_service, mock_alpha_repo, mock_perf_repo
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_perf_repo.get_latest.return_value = None

		performance_service.snapshot_market("IN")

		payload = mock_perf_repo.insert_snapshot.call_args[0][0]
		assert "diagnostics" not in payload
		assert set(payload) == {
			"market",
			"date",
			"as_of",
			"period",
			"portfolio_nav",
			"benchmark_nav",
			"holdings_count",
		}

	def test_repository_derives_date_when_caller_omits_it(self):
		repo = PortfolioPerformanceRepository()
		assert (
			repo._derive_date("2026-03-04T09:30:00+00:00") == "2026-03-04"
		)
		assert repo._derive_date(date(2026, 3, 4)) == "2026-03-04"
		assert repo._derive_date(datetime(2026, 3, 4, 9, 30)) == "2026-03-04"


class TestFirstNav:
	def test_first_nav_equals_100(
		self, performance_service, mock_alpha_repo, mock_perf_repo
	):
		mock_alpha_repo.get_all.return_value = []
		mock_perf_repo.get_latest.return_value = None

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["benchmark_nav"] == 100.0
		assert result["holdings_count"] == 0

	def test_first_snapshot_has_no_return(
		self, performance_service,
		mock_alpha_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "RELIANCE", "entry_price": 2500.0},
		]
		mock_perf_repo.get_latest.return_value = None
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("RELIANCE", 2700.0, 2500.0)
		]

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["benchmark_nav"] == 100.0
		assert "portfolio_return" not in result
		assert "benchmark_return" not in result


class TestCachedDailyReturn:
	def test_cached_prices_drive_nav(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "RELIANCE", "entry_price": 2500.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "RELIANCE", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("RELIANCE", 110.0, 100.0)
		]

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert result["holdings_count"] == 1

	def test_only_symbols_active_yesterday_contribute(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
			{"symbol": "NEW", "entry_price": 50.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "ABC", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 110.0, 100.0),
			cache_row("NEW", 25.0, 50.0),
		]

		result = performance_service.snapshot_market("IN")

		# ABC contributes +10%; NEW is a same-day addition and must
		# contribute nothing, so NAV is 110 and not the average of
		# +10% and -50%.
		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert result["holdings_count"] == 2

	def test_removed_stock_excluded_from_return(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "DEF", "action": "ADD"},
			{"symbol": "DEF", "action": "REMOVE"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 110.0, 100.0)
		]

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["holdings_count"] == 1

	def test_rotation_carries_nav_forward(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "NEW", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "OLD", "action": "ADD"},
			{"symbol": "OLD", "action": "REMOVE"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 105.0,
			"benchmark_nav": 102.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = []

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 105.0
		assert result["benchmark_nav"] == 102.0
		assert result["holdings_count"] == 1

	def test_nav_steps_once_per_trading_day(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "ABC", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"date": "2026-03-04",
			"portfolio_nav": 110.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 500.0, 100.0)
		]

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 3, 4, 18, 0, 0)
			result = performance_service.snapshot_market("IN")

		assert result["diagnostics"]["nav_step"] == "already_recorded_for_date"
		assert result["portfolio_nav"] == pytest.approx(110.0)
		mock_stock_data_repo.get_quote_data_many.assert_not_called()

	def test_stale_cache_is_not_chained(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "ABC", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 110.0, 100.0, updated_at="2026-01-16T10:00:00")
		]

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 1, 18, 12, 0, 0)
			result = performance_service.snapshot_market("IN")

		# A row from an earlier session must not be chained, otherwise
		# stale returns would compound indefinitely.
		assert result["portfolio_nav"] == 100.0
		assert result["diagnostics"]["stale_prices"] == ["ABC"]


class TestMissingCachedPrice:
	"""C: a missing cache row must never be papered over."""

	def test_missing_row_yields_no_return_and_no_fabrication(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "ABC", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = []

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert "ABC" in result["diagnostics"]["missing_prices"]
		assert result["diagnostics"]["missing_prices"] != []

	def test_partial_coverage_uses_only_available_prices(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "AAA", "entry_price": 100.0},
			{"symbol": "BBB", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "AAA", "action": "ADD"},
			{"symbol": "BBB", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("AAA", 110.0, 100.0)
		]

		result = performance_service.snapshot_market("IN")

		# Only AAA is priced, so the equal-weighted mean is AAA's return
		# alone rather than a diluted or invented figure. The absent
		# benchmark is reported too.
		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert "BBB" in result["diagnostics"]["missing_prices"]
		assert result["diagnostics"]["benchmark_status"] == "missing"

	def test_zero_and_null_prices_are_rejected(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "AAA", "entry_price": 100.0},
			{"symbol": "BBB", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "AAA", "action": "ADD"},
			{"symbol": "BBB", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("AAA", 0, 100.0),
			{"symbol": "BBB", "quote_json": {"current_price": 10.0}, "updated_at": datetime.utcnow().isoformat()},
		]

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert {"AAA", "BBB"} <= set(
			result["diagnostics"]["missing_prices"]
		)


class TestBenchmarkFromCache:
	"""D: benchmark prices come from the cache, never from Yahoo."""

	def test_cached_benchmark_compounds(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "RELIANCE", "entry_price": 2500.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "RELIANCE", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("RELIANCE", 110.0, 100.0),
			cache_row("^NSEI", 105.0, 100.0),
		]

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert result["benchmark_nav"] == pytest.approx(105.0)
		assert result["diagnostics"]["benchmark_status"] == "cached"

	def test_us_market_uses_ixic(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "AAPL", "entry_price": 150.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "AAPL", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("AAPL", 100.0, 100.0),
			cache_row("^IXIC", 102.0, 100.0),
		]

		result = performance_service.snapshot_market("US")

		assert result["benchmark_nav"] == pytest.approx(102.0)

	def test_absent_benchmark_carries_nav_and_reports(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "RELIANCE", "entry_price": 2500.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "RELIANCE", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 137.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("RELIANCE", 110.0, 100.0)
		]

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert result["benchmark_nav"] == 137.0
		assert result["diagnostics"]["benchmark_status"] == "missing"
		assert "^NSEI" in result["diagnostics"]["missing_prices"]


class TestEgressRegression:
	"""J: PerformanceService must perform no external market-data call."""

	def test_service_holds_no_market_data_provider(
		self, performance_service
	):
		assert not hasattr(performance_service, "market")
		assert hasattr(performance_service, "stock_data_repo")

	def test_service_source_has_no_external_provider_call(self):
		from pathlib import Path

		source = (
			Path(__file__).resolve().parents[1]
			/ "app"
			/ "services"
			/ "performance_service.py"
		).read_text(encoding="utf-8")

		for forbidden in (
			"self.market",
			"get_quote(",
			"get_historical_prices",
			"get_financials",
			"yahoo_service",
			"MarketDataService",
			"YahooProvider",
		):
			assert forbidden not in source, (
				"performance_service.py must not reference %s" % forbidden
			)

	def test_snapshot_makes_no_external_call_even_if_provider_present(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "ABC", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 110.0, 100.0)
		]

		class ExplodingProvider:
			def __getattr__(self, name):
				raise AssertionError(
					"external market-data call attempted: %s" % name
				)

		# Any provider reference introduced on the service would be a
		# MagicMock here, so assert on the call log instead.
		provider = ExplodingProvider()
		performance_service.market = provider
		try:
			performance_service.snapshot_market("IN")
		finally:
			del performance_service.market

		assert mock_stock_data_repo.get_quote_data_many.call_count == 1
		assert mock_perf_repo.insert_snapshot.call_count == 1

	def test_report_performance_makes_no_external_call(
		self, performance_service, mock_perf_repo
	):
		mock_perf_repo.get_for_period.return_value = [
			{"as_of": "2026-01-01T00:00:00", "portfolio_nav": 1.05, "benchmark_nav": 1.03, "holdings_count": 15},
			{"as_of": "2026-06-01T00:00:00", "portfolio_nav": 1.12, "benchmark_nav": 1.08, "holdings_count": 15},
		]

		result = performance_service.report_performance("IN", "1m")

		assert result["status"] == "available"
		mock_perf_repo.get_for_period.assert_called_once()
		assert not hasattr(performance_service, "market")


class TestRoundTrip:
	"""E: snapshot_market -> portfolio_performance -> get_latest."""

	def test_snapshot_is_readable_back(
		self,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_stock_data_repo,
	):
		store = {}

		class FakePerformanceRepo:
			def insert_snapshot(self, snapshot):
				store[snapshot["market"]] = snapshot
				return snapshot

			def get_latest(self, market):
				return store.get((market or "").upper())

		service = PerformanceService.__new__(PerformanceService)
		service.alpha_repo = mock_alpha_repo
		service.alpha_history_repo = mock_alpha_history_repo
		service.performance_repo = FakePerformanceRepo()
		service.stock_data_repo = mock_stock_data_repo

		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "ABC", "action": "ADD"},
		]
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 110.0, 100.0, updated_at="2026-03-04T18:00:00")
		]

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 3, 4, 18, 0, 0)
			service.snapshot_market("IN")

		latest = service.performance_repo.get_latest("IN")
		assert latest["portfolio_nav"] == pytest.approx(100.0)
		assert latest["date"] == "2026-03-04"
		assert latest["period"] == "INCEPTION"
		assert latest["holdings_count"] == 1

		# Second trading day: the stored row drives the next NAV step.
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 121.0, 110.0, updated_at="2026-03-05T18:00:00")
		]
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 3, 5, 18, 0, 0)
			service.snapshot_market("IN")

		assert service.performance_repo.get_latest("IN")["portfolio_nav"] == (
			pytest.approx(110.0)
		)
		assert service.performance_repo.get_latest("IN")["date"] == "2026-03-05"


class TestReportPeriods:
	def test_report_1m(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2026-08-21T00:00:00", "portfolio_nav": 1.05, "benchmark_nav": 1.03, "holdings_count": 15},
			{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.10, "benchmark_nav": 1.08, "holdings_count": 15},
		]
		result = performance_service.report_performance("IN", "1m")

		assert result["status"] == "available"
		assert result["portfolio_return"] == pytest.approx((1.10 / 1.05) - 1, rel=1e-2)
		assert result["benchmark_return"] == pytest.approx((1.08 / 1.03) - 1, rel=1e-2)
		assert result["excess_return"] == pytest.approx(
			((1.10 / 1.05) - 1) - ((1.08 / 1.03) - 1), rel=1e-2
		)

	def test_report_6m(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2026-03-21T00:00:00", "portfolio_nav": 1.00, "benchmark_nav": 1.00, "holdings_count": 15},
			{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.20, "benchmark_nav": 1.15, "holdings_count": 15},
		]
		result = performance_service.report_performance("IN", "6m")

		assert result["status"] == "available"
		assert result["portfolio_return"] == pytest.approx(0.20)
		assert result["benchmark_return"] == pytest.approx(0.15)

	def test_report_1y(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "US", "as_of": "2025-09-21T00:00:00", "portfolio_nav": 1.00, "benchmark_nav": 1.00, "holdings_count": 15},
			{"market": "US", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.25, "benchmark_nav": 1.20, "holdings_count": 15},
		]
		result = performance_service.report_performance("US", "1y")

		assert result["status"] == "available"
		assert result["benchmark"] == "^IXIC"
		assert result["portfolio_return"] == pytest.approx(0.25)
		assert result["benchmark_return"] == pytest.approx(0.20)

	def test_report_since_inception(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2024-01-01T00:00:00", "portfolio_nav": 1.00, "benchmark_nav": 1.00, "holdings_count": 15},
			{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.50, "benchmark_nav": 1.30, "holdings_count": 15},
		]
		result = performance_service.report_performance("IN", "since-inception")

		assert result["status"] == "available"
		assert result["portfolio_return"] == pytest.approx(0.50)
		assert result["benchmark_return"] == pytest.approx(0.30)
		assert result["excess_return"] == pytest.approx(0.20)

	def test_report_fy2025_26(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2025-04-01T00:00:00", "portfolio_nav": 1.00, "benchmark_nav": 1.00, "holdings_count": 15},
			{"market": "IN", "as_of": "2026-03-31T00:00:00", "portfolio_nav": 1.12, "benchmark_nav": 1.08, "holdings_count": 15},
		]
		result = performance_service.report_performance("IN", "fy2025-26")

		assert result["status"] == "available"
		assert result["portfolio_return"] == pytest.approx(0.12)
		assert result["benchmark_return"] == pytest.approx(0.08)

	def test_report_not_available_when_no_snapshots(
		self, performance_service, mock_perf_repo
	):
		mock_perf_repo.get_for_period.return_value = []
		result = performance_service.report_performance("IN", "1m")

		assert result["status"] == "not_available"
		assert result["portfolio_return"] is None

	def test_report_not_available_when_one_snapshot(
		self, performance_service, mock_perf_repo
	):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.10, "benchmark_nav": 1.08, "holdings_count": 15},
		]
		result = performance_service.report_performance("IN", "1m")

		assert result["status"] == "not_available"
		assert "Insufficient" in result["reason"]

	def test_report_invalid_period(self, performance_service, mock_perf_repo):
		result = performance_service.report_performance("IN", "invalid")

		assert result["status"] == "error"
		assert "Invalid period" in result["detail"]

	def test_report_us_uses_ixic(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "US", "as_of": "2025-01-01T00:00:00", "portfolio_nav": 1.00, "benchmark_nav": 1.00, "holdings_count": 15},
			{"market": "US", "as_of": "2026-01-01T00:00:00", "portfolio_nav": 1.20, "benchmark_nav": 1.15, "holdings_count": 15},
		]
		result = performance_service.report_performance("US", "1y")

		assert result["benchmark"] == "^IXIC"


class TestIndUsIndependence:
	def test_in_and_us_have_separate_nav(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.side_effect = [
			[{"symbol": "RELIANCE", "entry_price": 2500.0}],
			[{"symbol": "AAPL", "entry_price": 150.0}],
		]
		mock_alpha_history_repo.get_events_up_to.side_effect = [
			[{"symbol": "RELIANCE", "action": "ADD"}],
			[{"symbol": "AAPL", "action": "ADD"}],
		]
		mock_perf_repo.get_latest.side_effect = [
			{"portfolio_nav": 100.0, "benchmark_nav": 100.0},
			{"portfolio_nav": 100.0, "benchmark_nav": 100.0},
		]
		mock_stock_data_repo.get_quote_data_many.side_effect = [
			[cache_row("RELIANCE", 110.0, 100.0)],
			[cache_row("AAPL", 100.0, 100.0)],
		]

		result_in = performance_service.snapshot_market("IN")
		result_us = performance_service.snapshot_market("US")

		assert result_in["portfolio_nav"] == pytest.approx(110.0)
		assert result_us["portfolio_nav"] == pytest.approx(100.0)
		assert result_in["market"] == "IN"
		assert result_us["market"] == "US"


class TestNoReconstruction:
	def test_uses_alpha_history_for_active_symbols(
		self,
		performance_service,
		mock_alpha_repo,
		mock_alpha_history_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = []
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = [
			cache_row("ABC", 110.0, 100.0)
		]

		result = performance_service.snapshot_market("IN")

		mock_alpha_history_repo.get_events_up_to.assert_called_once()
		assert result["portfolio_nav"] == 100.0

	def test_holdings_count_is_current_portfolio_size(
		self,
		performance_service,
		mock_alpha_repo,
		mock_perf_repo,
		mock_stock_data_repo,
	):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "A", "entry_price": 100.0},
			{"symbol": "B", "entry_price": 200.0},
			{"symbol": "C", "entry_price": 300.0},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_stock_data_repo.get_quote_data_many.return_value = []

		result = performance_service.snapshot_market("IN")

		assert result["holdings_count"] == 3


class TestEmptyPortfolio:
	def test_empty_portfolio_keeps_last_nav(
		self, performance_service, mock_alpha_repo, mock_perf_repo
	):
		mock_alpha_repo.get_all.return_value = []
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 105.0,
			"benchmark_nav": 102.0,
		}

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 105.0
		assert result["benchmark_nav"] == 102.0
		assert result["holdings_count"] == 0
		assert result["diagnostics"]["nav_step"] == "no_holdings"


class TestRepository:
	def test_valid_periods(self):
		repo = PortfolioPerformanceRepository()
		assert repo.TABLE == "portfolio_performance"


if __name__ == "__main__":
	pytest.main([__file__, "-v"])
