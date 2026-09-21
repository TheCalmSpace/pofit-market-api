import pytest
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, patch

from app.services.performance_service import PerformanceService
from app.repositories.portfolio_performance_repository import PortfolioPerformanceRepository


class MockHistoricalPrice:
	def __init__(self, d: date, close: float):
		self.date = d
		self.close = close


class MockQuote:
	def __init__(self, current_price: float):
		self.current_price = current_price


class MockQuoteResponse:
	def __init__(self, current_price: float):
		self.current_price = current_price


class MockPayload:
	def __init__(self, current_price: float):
		self.quote_json = {"current_price": current_price}


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
def mock_market_service():
	market = MagicMock()
	market.yahoo = MagicMock()
	return market


@pytest.fixture
def performance_service(mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo, mock_market_service):
	with patch("app.services.performance_service.AlphaPortfolioRepository", return_value=mock_alpha_repo):
		with patch("app.services.performance_service.AlphaHistoryRepository", return_value=mock_alpha_history_repo):
			with patch("app.services.performance_service.PortfolioPerformanceRepository", return_value=mock_perf_repo):
				with patch("app.services.performance_service.MarketDataService", return_value=mock_market_service):
					service = PerformanceService()
					service.alpha_repo = mock_alpha_repo
					service.alpha_history_repo = mock_alpha_history_repo
					service.performance_repo = mock_perf_repo
					service.market = mock_market_service
					return service


class TestFirstNav:
	def test_first_nav_equals_100(self, performance_service, mock_alpha_repo, mock_perf_repo):
		mock_alpha_repo.get_all.return_value = []
		mock_perf_repo.get_latest.return_value = None

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["benchmark_nav"] == 100.0
		assert result["holdings_count"] == 0

	def test_first_snapshot_has_no_return(self, performance_service, mock_alpha_repo, mock_perf_repo):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "RELIANCE", "entry_price": 2500.0, "entry_date": "2024-01-15T00:00:00+00:00"},
		]
		mock_perf_repo.get_latest.return_value = None
		mock_market = MagicMock()
		mock_market.yahoo.get_quote.return_value = MockQuote(current_price=2700.0)
		mock_market.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2024, 1, 15), 2500.0)]
		)
		performance_service.market = mock_market

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["benchmark_nav"] == 100.0
		assert "portfolio_return" not in result
		assert "benchmark_return" not in result


class TestExistingStockDailyReturn:
	def test_existing_stock_daily_return_compounds_nav(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo, mock_market_service):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "RELIANCE", "entry_price": 2500.0, "entry_date": "2024-01-15T00:00:00+00:00"},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "RELIANCE", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_market_service.yahoo.get_quote.return_value = MockQuote(current_price=110.0)
		mock_market_service.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 20), 100.0)]
		)
		performance_service.market = mock_market_service

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 1, 21, 12, 0, 0)
			result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert result["benchmark_nav"] == pytest.approx(110.0)
		assert result["holdings_count"] == 1


class TestNewStockEntry:
	def test_new_stock_no_first_day_return(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo, mock_market_service):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0, "entry_date": "2026-01-01T00:00:00+00:00"},
			{"symbol": "NEW", "entry_price": 50.0, "entry_date": "2026-09-20T00:00:00+00:00"},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "ABC", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_market_service.yahoo.get_quote.side_effect = [
			MockQuote(current_price=110.0),
			MockQuote(current_price=60.0),
			MockQuote(current_price=105.0),
		]
		mock_market_service.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 19), 100.0)]
		)
		performance_service.market = mock_market_service

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["holdings_count"] == 2


class TestRemovedStockExit:
	def test_removed_stock_excluded_from_return(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0, "entry_date": "2026-01-01T00:00:00+00:00"},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "DEF", "action": "ADD"},
			{"symbol": "DEF", "action": "REMOVE"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_market = MagicMock()
		mock_market.yahoo.get_quote.return_value = MockQuote(current_price=110.0)
		mock_market.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 19), 100.0)]
		)
		performance_service.market = mock_market

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["holdings_count"] == 1


class TestRotation:
	def test_rotation_nav_persists(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo, mock_market_service):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "NEW", "entry_price": 100.0, "entry_date": "2026-09-20T00:00:00+00:00"},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "OLD", "action": "ADD"},
			{"symbol": "OLD", "action": "REMOVE"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 105.0,
			"benchmark_nav": 102.0,
		}
		mock_market_service.yahoo.get_quote.return_value = MockQuote(current_price=110.0)
		mock_market_service.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 19), 100.0)]
		)
		performance_service.market = mock_market_service

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 105.0
		assert result["benchmark_nav"] == 102.0
		assert result["holdings_count"] == 1


class TestWeekendHandling:
	def test_weekend_no_return_on_non_trading_day(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo, mock_market_service):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0, "entry_date": "2026-01-01T00:00:00+00:00"},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [{"symbol": "ABC", "action": "ADD"}]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_market_service.yahoo.get_quote.return_value = MockQuote(current_price=110.0)
		mock_market_service.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 16), 100.0)]
		)
		performance_service.market = mock_market_service

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 1, 18, 12, 0, 0)
			result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 100.0
		assert result["benchmark_nav"] == 100.0


class TestBenchmarkNav:
	def test_benchmark_nav_compounds(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo, mock_market_service):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "RELIANCE", "entry_price": 2500.0, "entry_date": "2024-01-15T00:00:00+00:00"},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = [
			{"symbol": "RELIANCE", "action": "ADD"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_market_service.yahoo.get_quote.side_effect = [
			MockQuote(current_price=110.0),
			MockQuote(current_price=110.0),
		]
		mock_market_service.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 20), 100.0)]
		)
		performance_service.market = mock_market_service

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 1, 21, 12, 0, 0)
			result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == pytest.approx(110.0)
		assert result["benchmark_nav"] == pytest.approx(110.0)
		assert result["holdings_count"] == 1


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

	def test_report_not_available_when_no_snapshots(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = []
		result = performance_service.report_performance("IN", "1m")

		assert result["status"] == "not_available"
		assert result["portfolio_return"] is None

	def test_report_not_available_when_one_snapshot(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2026-09-21T00:00:00", "portfolio_nav": 1.10, "benchmark_nav": 1.08, "holdings_count": 15},
		]
		result = performance_service.report_performance("IN", "1m")

		assert result["status"] == "not_available"
		assert "Insufficient" in result["reason"]

	def test_report_no_yahoo_calls(self, performance_service, mock_perf_repo):
		mock_perf_repo.get_for_period.return_value = [
			{"market": "IN", "as_of": "2026-01-01T00:00:00", "portfolio_nav": 1.05, "benchmark_nav": 1.03, "holdings_count": 15},
			{"market": "IN", "as_of": "2026-06-01T00:00:00", "portfolio_nav": 1.12, "benchmark_nav": 1.08, "holdings_count": 15},
		]

		result = performance_service.report_performance("IN", "1m")

		assert result["status"] == "available"
		mock_perf_repo.get_for_period.assert_called_once()
		performance_service.market.yahoo.get_quote.assert_not_called()
		performance_service.market.yahoo.get_historical_prices.assert_not_called()

	def test_report_invalid_period(self, performance_service, mock_perf_repo, mock_market_service):
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
	def test_in_and_us_have_separate_nav(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo, mock_market_service):
		mock_alpha_repo.get_all.side_effect = [
			[{"symbol": "RELIANCE", "entry_price": 2500.0, "entry_date": "2024-01-15T00:00:00+00:00"}],
			[{"symbol": "AAPL", "entry_price": 150.0, "entry_date": "2024-01-15T00:00:00+00:00"}],
		]
		mock_alpha_history_repo.get_events_up_to.side_effect = [
			[{"symbol": "RELIANCE", "action": "ADD"}],
			[{"symbol": "AAPL", "action": "ADD"}],
		]
		mock_perf_repo.get_latest.side_effect = [
			{"portfolio_nav": 100.0, "benchmark_nav": 100.0},
			{"portfolio_nav": 100.0, "benchmark_nav": 100.0},
		]
		mock_market_service.yahoo.get_quote.side_effect = [
			MockQuote(current_price=110.0),
			MockQuote(current_price=110.0),
			MockQuote(current_price=110.0),
			MockQuote(current_price=110.0),
		]
		mock_market_service.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 20), 100.0)]
		)
		performance_service.market = mock_market_service

		with patch("app.services.performance_service.datetime") as mock_dt:
			mock_dt.utcnow.return_value = datetime(2026, 1, 21, 12, 0, 0)
			result_in = performance_service.snapshot_market("IN")
			result_us = performance_service.snapshot_market("US")

		assert result_in["portfolio_nav"] == pytest.approx(110.0)
		assert result_us["portfolio_nav"] == pytest.approx(110.0)
		assert result_in["holdings_count"] == 1
		assert result_us["holdings_count"] == 1


class TestNoReconstruction:
	def test_uses_alpha_history_for_active_symbols(self, performance_service, mock_alpha_repo, mock_alpha_history_repo, mock_perf_repo):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "ABC", "entry_price": 100.0, "entry_date": "2026-01-01T00:00:00+00:00"},
		]
		mock_alpha_history_repo.get_events_up_to.return_value = []
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_market = MagicMock()
		mock_market.yahoo.get_quote.return_value = MockQuote(current_price=110.0)
		performance_service.market = mock_market

		result = performance_service.snapshot_market("IN")

		mock_alpha_history_repo.get_events_up_to.assert_called_once()
		assert result["portfolio_nav"] == 100.0

	def test_holdings_count_is_current_portfolio_size(self, performance_service, mock_alpha_repo, mock_perf_repo):
		mock_alpha_repo.get_all.return_value = [
			{"symbol": "A", "entry_price": 100.0, "entry_date": "2026-01-01T00:00:00+00:00"},
			{"symbol": "B", "entry_price": 200.0, "entry_date": "2026-01-01T00:00:00+00:00"},
			{"symbol": "C", "entry_price": 300.0, "entry_date": "2026-01-01T00:00:00+00:00"},
		]
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 100.0,
			"benchmark_nav": 100.0,
		}
		mock_market = MagicMock()
		mock_market.yahoo.get_quote.return_value = MockQuote(current_price=110.0)
		mock_market.yahoo.get_historical_prices.return_value = MagicMock(
			prices=[MockHistoricalPrice(date(2026, 1, 20), 100.0)]
		)
		performance_service.market = mock_market

		result = performance_service.snapshot_market("IN")

		assert result["holdings_count"] == 3


class TestEmptyPortfolio:
	def test_empty_portfolio_keeps_last_nav(self, performance_service, mock_alpha_repo, mock_perf_repo):
		mock_alpha_repo.get_all.return_value = []
		mock_perf_repo.get_latest.return_value = {
			"portfolio_nav": 105.0,
			"benchmark_nav": 102.0,
		}

		result = performance_service.snapshot_market("IN")

		assert result["portfolio_nav"] == 105.0
		assert result["benchmark_nav"] == 102.0
		assert result["holdings_count"] == 0


class TestRepository:
	def test_valid_periods(self):
		repo = PortfolioPerformanceRepository()
		assert repo.TABLE == "portfolio_performance"


if __name__ == "__main__":
	pytest.main([__file__, "-v"])
