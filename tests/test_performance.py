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
			mock_dt.utcnow.return_value = datetime(2026, 1, 19, 12, 0, 0)
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
		# Mock datetime to a fixed date so calendar calculations are deterministic
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 9, 21, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
			
			# For 1m (21 trading days) from 2026-09-21:
			# latest_trading_day = 2026-09-21 (Monday)
			# requested_start = 21 trading days before = ~2026-08-22
			# requested_end = 2026-09-21
			mock_perf_repo.get_for_period.return_value = [
				{"as_of": "2026-08-24T00:00:00", "portfolio_nav": 1.05, "benchmark_nav": 1.03, "holdings_count": 15},
				{"as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.12, "benchmark_nav": 1.08, "holdings_count": 15},
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
		# Mock datetime to 2026-09-21 (Monday) for deterministic calendar
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 9, 21, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
			
			# For 1m (21 trading days) from 2026-09-21:
			# latest_trading_day = 2026-09-21
			# requested_start = 21 trading days before = 2026-08-24 (Monday)
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-08-24T00:00:00", "portfolio_nav": 1.05, "benchmark_nav": 1.03, "holdings_count": 15},
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
		# Mock datetime to 2026-09-21 (Monday) for deterministic calendar
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 9, 21, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
			
			# For 6m (126 trading days) from 2026-09-21:
			# latest_trading_day = 2026-09-21
			# requested_start = 126 trading days before = 2026-03-30 (Monday)
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-03-30T00:00:00", "portfolio_nav": 1.00, "benchmark_nav": 1.00, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.20, "benchmark_nav": 1.15, "holdings_count": 15},
			]
			result = performance_service.report_performance("IN", "6m")

			assert result["status"] == "available"
			assert result["portfolio_return"] == pytest.approx(0.20)
			assert result["benchmark_return"] == pytest.approx(0.15)

	def test_report_1y(self, performance_service, mock_perf_repo):
		# Mock datetime to 2026-09-21 (Monday) for deterministic calendar
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 9, 21, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
			
			# For 1y (252 trading days) from 2026-09-21:
			# latest_trading_day = 2026-09-21
			# requested_start = 252 trading days before = 2025-10-03 (Friday)
			mock_perf_repo.get_for_period.return_value = [
				{"market": "US", "as_of": "2025-10-03T00:00:00", "portfolio_nav": 1.00, "benchmark_nav": 1.00, "holdings_count": 15},
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

	# --- Regression tests for period coverage and unit consistency ---

	def test_sparse_snapshots_do_not_yield_identical_returns_across_periods(
		self, performance_service, mock_perf_repo
	):
		"""
		With only 5 trading days of data near 2026-10-06,
		requesting 1m, 3m, 6m, 1y should all return not_available,
		not identical available returns.
		"""
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 10, 6, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

			# Calendar for 1m from 2026-10-06: requested_start ≈ 2026-09-05 (21 trading days back)
			# But we only have data from 2026-10-02 to 2026-10-06 (5 trading days)
			# So start_distance will be large -> not_available
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-10-02T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-10-03T00:00:00", "portfolio_nav": 100.5, "benchmark_nav": 99.8, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-10-04T00:00:00", "portfolio_nav": 101.0, "benchmark_nav": 99.5, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-10-05T00:00:00", "portfolio_nav": 101.5, "benchmark_nav": 99.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-10-06T00:00:00", "portfolio_nav": 102.0, "benchmark_nav": 98.5, "holdings_count": 15},
			]

			# 1m = 21 trading days needed, data starts ~18 trading days after requested start -> not_available
			result_1m = performance_service.report_performance("IN", "1m")
			assert result_1m["status"] == "not_available"
			assert "Insufficient history" in result_1m["reason"] or "Insufficient data span" in result_1m["reason"]

			# 3m = 63 trading days needed -> not_available
			result_3m = performance_service.report_performance("IN", "3m")
			assert result_3m["status"] == "not_available"

			# 6m = 126 trading days needed -> not_available
			result_6m = performance_service.report_performance("IN", "6m")
			assert result_6m["status"] == "not_available"

			# 1y = 252 trading days needed -> not_available
			result_1y = performance_service.report_performance("IN", "1y")
			assert result_1y["status"] == "not_available"

			# since-inception: needs data from 2026-09-14, only have from 2026-10-02 -> not_available
			result_si = performance_service.report_performance("IN", "since-inception")
			assert result_si["status"] == "not_available"
			assert "Insufficient inception history" in result_si["reason"]

	def test_insufficient_coverage_returns_not_available(
		self, performance_service, mock_perf_repo
	):
		"""Period with data spanning only 10 trading days fails 1m (21 days) check."""
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 9, 21, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

			# Calendar for 1m from 2026-09-21: requested_start = 2026-08-24
			# We provide data starting at 2026-09-08 (11 trading days after requested start)
			# This exceeds MAX_START_TOLERANCE=2 -> not_available
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-09-08T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 105.0, "benchmark_nav": 103.0, "holdings_count": 15},
			]

			result = performance_service.report_performance("IN", "1m")
			assert result["status"] == "not_available"
			assert "Insufficient history" in result["reason"] or "Insufficient data span" in result["reason"]

	def test_sufficient_coverage_produces_correct_interval_return(
		self, performance_service, mock_perf_repo
	):
		"""21 trading days of data yields valid 1m return."""
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 9, 21, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

			# 21 trading days of data: 2026-08-24 to 2026-09-21
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-08-24T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 110.0, "benchmark_nav": 105.0, "holdings_count": 15},
			]

			result = performance_service.report_performance("IN", "1m")
			assert result["status"] == "available"
			assert result["portfolio_return"] == pytest.approx(0.10)
			assert result["benchmark_return"] == pytest.approx(0.05)
			assert result["excess_return"] == pytest.approx(0.05)
			assert result["alpha"] == pytest.approx(5.0)  # percentage

	def test_missing_september_15_gap_does_not_fabricate_returns(
		self, performance_service, mock_perf_repo
	):
		"""
		The 2026-09-15 India data gap (no prices) should not cause
		fabricated prices or returns. The span check uses actual
		snapshot dates, so a gap in the middle doesn't affect the
		start/end span calculation.
		"""
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 9, 21, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

			# Data from 2026-08-24 (requested_start for 1m) to 2026-09-21
			# with a gap at 2026-09-15 (no data for that day)
			# This is 21 trading days apart (minus the gap day)
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-08-24T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-08-25T00:00:00", "portfolio_nav": 100.5, "benchmark_nav": 99.8, "holdings_count": 15},
				# ... gap at 2026-09-15 (no row)
				{"market": "IN", "as_of": "2026-09-16T00:00:00", "portfolio_nav": 101.0, "benchmark_nav": 99.5, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-09-17T00:00:00", "portfolio_nav": 101.5, "benchmark_nav": 99.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-09-18T00:00:00", "portfolio_nav": 102.0, "benchmark_nav": 98.5, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 103.0, "benchmark_nav": 98.0, "holdings_count": 15},
			]

			# 1m needs 21 trading days, we have ~20 trading days of data (one gap day)
			# With tolerance of 2 at each end, this should be available
			result = performance_service.report_performance("IN", "1m")
			# The gap doesn't cause fabrication - we just use actual start/end dates
			assert result["status"] == "available"
			assert result["date_start"] == "2026-08-24"
			assert result["date_end"] == "2026-09-21"
			# Return is computed from actual first and last snapshot, gap is ignored
			assert result["portfolio_return"] == pytest.approx(0.03)

	def test_since_inception_respects_official_inception_date(
		self, performance_service, mock_perf_repo
	):
		"""
		Since-inception must use the official inception date (2026-09-12)
		and not silently use a later baseline while claiming full coverage.
		"""
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 10, 6, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

			# Data starts at 2026-09-14 (first reliable trading day), not 2026-09-12
			# This is within 2 trading days tolerance, so start check passes
			# But we need sufficient span from 2026-09-14 to 2026-10-06
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-09-14T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-10-06T00:00:00", "portfolio_nav": 105.0, "benchmark_nav": 103.0, "holdings_count": 15},
			]

			result = performance_service.report_performance("IN", "since-inception")
			# Should be available since span is ~16 trading days > 5 minimum
			assert result["status"] == "available"
			assert result["date_start"] == "2026-09-14"
			assert result["date_end"] == "2026-10-06"
			assert result["portfolio_return"] == pytest.approx(0.05)

	def test_since_inception_rejects_late_baseline_masquerading_as_full_coverage(
		self, performance_service, mock_perf_repo
	):
		"""
		If data starts significantly after official inception (e.g., 2026-10-01),
		since-inception should return not_available, not claim full coverage.
		"""
		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 10, 6, 12, 0, 0)
			mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

			# Data starts at 2026-10-01, well after official inception 2026-09-12
			mock_perf_repo.get_for_period.return_value = [
				{"market": "IN", "as_of": "2026-10-01T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
				{"market": "IN", "as_of": "2026-10-06T00:00:00", "portfolio_nav": 102.0, "benchmark_nav": 101.0, "holdings_count": 15},
			]

			result = performance_service.report_performance("IN", "since-inception")
			assert result["status"] == "not_available"
			assert "Insufficient inception history" in result["reason"]

	def test_endpoints_reconcile_after_unit_conversion(
		self, performance_service, mock_perf_repo
	):
		"""
		/alpha/performance (summary) returns percentage alpha.
		/alpha/performance-report returns fraction excess_return and percentage alpha.
		They should be mathematically consistent: alpha_pct = excess_return * 100.
		"""
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2026-09-14T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
			{"market": "IN", "as_of": "2026-10-06T00:00:00", "portfolio_nav": 110.0, "benchmark_nav": 105.0, "holdings_count": 15},
		]

		# Direct call to report_performance (used by /alpha/performance-report)
		report = performance_service.report_performance("IN", "since-inception")

		# The report should have both excess_return (fraction) and alpha (percentage)
		assert "excess_return" in report
		assert "alpha" in report
		assert report["excess_return"] is not None
		assert report["alpha"] is not None
		assert report["alpha"] == pytest.approx(report["excess_return"] * 100.0)

		# Simulate _build_performance_summary (used by /alpha/performance)
		portfolio_pct = report["portfolio_return"] * 100.0 if report["portfolio_return"] is not None else None
		benchmark_pct = report["benchmark_return"] * 100.0 if report["benchmark_return"] is not None else None
		alpha_pct = portfolio_pct - benchmark_pct if portfolio_pct is not None and benchmark_pct is not None else None

		assert alpha_pct == pytest.approx(report["alpha"])
		assert alpha_pct == pytest.approx(report["excess_return"] * 100.0)

	def test_response_includes_alpha_field_in_report(
		self, performance_service, mock_perf_repo
	):
		"""AlphaPerformanceReportResponse now includes alpha (percentage) field."""
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2026-09-14T00:00:00", "portfolio_nav": 100.0, "benchmark_nav": 100.0, "holdings_count": 15},
			{"market": "IN", "as_of": "2026-10-06T00:00:00", "portfolio_nav": 110.0, "benchmark_nav": 105.0, "holdings_count": 15},
		]

		result = performance_service.report_performance("IN", "since-inception")
		assert "alpha" in result
		assert result["alpha"] == pytest.approx(5.0)  # 10% - 5% = 5%

	def test_not_available_includes_alpha_null(
		self, performance_service, mock_perf_repo
	):
		"""Not available response includes alpha: null for consistency."""
		mock_perf_repo.get_for_period.return_value = []
		result = performance_service.report_performance("IN", "1m")
		assert result["status"] == "not_available"
		assert result["alpha"] is None
		assert result["excess_return"] is None
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
