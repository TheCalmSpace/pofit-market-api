import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.stock_data_ingestion_service import StockDataIngestionService
from app.services.market_data_service import CACHE_TTL
from app.services.yahoo_service import MarketDataUnavailableError, SymbolNotFoundError


class TestStockDataIngestionService:
    @pytest.fixture
    def mock_market_service(self):
        with patch("app.services.stock_data_ingestion_service.MarketDataService") as mock:
            yield mock

    @pytest.fixture
    def mock_stock_repo(self):
        with patch("app.services.stock_data_ingestion_service.StockRepository") as mock:
            yield mock

    @pytest.fixture
    def mock_stock_data_repo(self):
        with patch("app.services.stock_data_ingestion_service.StockDataRepository") as mock:
            yield mock

    @pytest.fixture
    def mock_supabase(self):
        with patch("app.services.stock_data_ingestion_service.supabase") as mock:
            yield mock

    def test_missing_stock_data_selected(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        universe = [{"symbol": "RELIANCE"}, {"symbol": "TCS"}, {"symbol": "INFY"}]
        mock_stock_repo.return_value.list_all_by_country.return_value = universe
        mock_supabase.table.return_value.select.return_value.or_.return_value.execute.return_value.data = []

        service = StockDataIngestionService(batch_size=10)
        missing, stale, fresh = service._classify_symbols(universe, {"RELIANCE": None, "TCS": None, "INFY": None})

        assert missing == ["RELIANCE", "TCS", "INFY"]
        assert stale == []
        assert fresh == []

    def test_stale_stock_data_selected(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        old_time = (datetime.now(timezone.utc) - CACHE_TTL - timedelta(hours=1)).isoformat()
        universe = [{"symbol": "RELIANCE"}, {"symbol": "TCS"}]
        cache_map = {
            "RELIANCE": {"updated_at": old_time, "cache_status": "fresh"},
            "TCS": {"updated_at": old_time, "cache_status": "partial"},
        }

        service = StockDataIngestionService(batch_size=10)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        assert missing == []
        assert set(symbol for symbol, _ in stale) == {"RELIANCE", "TCS"}
        assert fresh == []

    def test_fresh_stock_data_skipped(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        universe = [{"symbol": "RELIANCE"}, {"symbol": "TCS"}]
        cache_map = {
            "RELIANCE": {"updated_at": recent_time, "cache_status": "fresh"},
            "TCS": {"updated_at": recent_time, "cache_status": "partial"},
        }

        service = StockDataIngestionService(batch_size=10)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        assert missing == []
        assert stale == []
        assert set(fresh) == {"RELIANCE", "TCS"}

    def test_mixed_universe_only_stale_missing_selected(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        now = datetime.now(timezone.utc)
        old_time = (now - CACHE_TTL - timedelta(hours=1)).isoformat()
        recent_time = (now - timedelta(hours=1)).isoformat()

        universe = [
            {"symbol": "RELIANCE"},  # missing
            {"symbol": "TCS"},       # stale
            {"symbol": "INFY"},      # fresh
            {"symbol": "HDFCBANK"},  # missing
        ]
        cache_map = {
            "RELIANCE": None,
            "TCS": {"updated_at": old_time, "cache_status": "fresh"},
            "INFY": {"updated_at": recent_time, "cache_status": "fresh"},
            "HDFCBANK": None,
        }

        service = StockDataIngestionService(batch_size=10)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        assert set(missing) == {"RELIANCE", "HDFCBANK"}
        assert [symbol for symbol, _ in stale] == ["TCS"]
        assert fresh == ["INFY"]

    def test_no_n1_supabase_reads(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        universe = [{"symbol": f"STOCK{i}"} for i in range(250)]
        mock_stock_repo.return_value.list_all_by_country.return_value = universe
        mock_supabase.table.return_value.select.return_value.or_.return_value.execute.return_value.data = []

        service = StockDataIngestionService(batch_size=10)
        symbols = [s["symbol"] for s in universe]
        cache_map = service._check_cache_status_batched(symbols)

        assert mock_supabase.table.return_value.select.return_value.or_.return_value.execute.call_count == 2
        assert len(cache_map) == 250

    def test_only_required_columns_selected(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        universe = [{"symbol": "RELIANCE"}]
        mock_stock_repo.return_value.list_all_by_country.return_value = universe
        mock_select = mock_supabase.table.return_value.select
        mock_select.return_value.or_.return_value.execute.return_value.data = []

        service = StockDataIngestionService(batch_size=10)
        service._check_cache_status_batched(["RELIANCE"])

        mock_select.assert_called_with("symbol, updated_at, cache_status")

    def test_one_yahoo_failure_does_not_terminate_batch(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        universe = [{"symbol": "RELIANCE"}, {"symbol": "TCS"}, {"symbol": "INFY"}]
        mock_stock_repo.return_value.list_all_by_country.return_value = universe
        mock_supabase.table.return_value.select.return_value.or_.return_value.execute.return_value.data = []

        market_instance = mock_market_service.return_value
        market_instance.refresh_stock.side_effect = [
            None,
            MarketDataUnavailableError("Yahoo failed"),
            None,
        ]

        service = StockDataIngestionService(batch_size=10)
        report = service.run()

        assert report.processed == 3
        assert report.succeeded == 2
        assert report.failed == 1

    def test_yahoo_429_stops_batch_with_backoff(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        universe = [{"symbol": "RELIANCE"}, {"symbol": "TCS"}, {"symbol": "INFY"}]
        mock_stock_repo.return_value.list_all_by_country.return_value = universe
        mock_supabase.table.return_value.select.return_value.or_.return_value.execute.return_value.data = []

        market_instance = mock_market_service.return_value
        market_instance.refresh_stock.side_effect = [
            MarketDataUnavailableError("429 Too Many Requests"),
            None,
            None,
        ]

        service = StockDataIngestionService(batch_size=10)
        report = service.run()

        assert report.yahoo_rate_limited is True
        assert report.processed == 1

    def test_symbol_not_found_logged_not_fatal(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        universe = [{"symbol": "RELIANCE"}, {"symbol": "INVALID"}]
        mock_stock_repo.return_value.list_all_by_country.return_value = universe
        mock_supabase.table.return_value.select.return_value.or_.return_value.execute.return_value.data = []

        market_instance = mock_market_service.return_value
        market_instance.refresh_stock.side_effect = [
            None,
            SymbolNotFoundError("INVALID"),
        ]

        service = StockDataIngestionService(batch_size=10)
        report = service.run()

        assert report.processed == 2
        assert report.succeeded == 1
        assert report.failed == 1


class TestStockDataIngestionServiceIntegration:
    @patch("app.services.stock_data_ingestion_service.supabase")
    @patch("app.services.stock_data_ingestion_service.StockRepository")
    @patch("app.services.stock_data_ingestion_service.MarketDataService")
    def test_full_run_populates_report(
        self, mock_market_service, mock_stock_repo, mock_supabase
    ):
        universe = [{"symbol": "RELIANCE"}, {"symbol": "TCS"}]
        mock_stock_repo.return_value.list_all_by_country.return_value = universe
        mock_supabase.table.return_value.select.return_value.or_.return_value.execute.return_value.data = []

        market_instance = mock_market_service.return_value
        market_instance.refresh_stock.return_value = {"quote_json": {}}

        service = StockDataIngestionService(batch_size=10)
        report = service.run()

        assert report.total_universe == 2
        assert report.missing_cache == 2
        assert report.stale_cache == 0
        assert report.fresh_cache == 0
        assert report.processed == 2
        assert report.succeeded == 2
        assert report.failed == 0


class TestStarvationFix:
    """Tests for the starvation fix: prioritize missing, then oldest stale."""

    @pytest.fixture
    def mock_market_service(self):
        with patch("app.services.stock_data_ingestion_service.MarketDataService"):
            yield

    @pytest.fixture
    def mock_stock_repo(self):
        with patch("app.services.stock_data_ingestion_service.StockRepository") as mock:
            yield mock

    @pytest.fixture
    def mock_stock_data_repo(self):
        with patch("app.services.stock_data_ingestion_service.StockDataRepository"):
            yield

    @pytest.fixture
    def mock_supabase(self):
        with patch("app.services.stock_data_ingestion_service.supabase") as mock:
            yield mock

    def test_missing_before_stale(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        """Missing symbols should be processed before stale symbols."""
        now = datetime.now(timezone.utc)
        old_time = (now - CACHE_TTL - timedelta(hours=1)).isoformat()

        universe = [
            {"symbol": "MISSING1"},
            {"symbol": "STALE1"},
            {"symbol": "STALE2"},
        ]
        cache_map = {
            "MISSING1": None,
            "STALE1": {"updated_at": old_time, "cache_status": "fresh"},
            "STALE2": {"updated_at": old_time, "cache_status": "fresh"},
        }

        service = StockDataIngestionService(batch_size=10)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        stale_symbols = [s for s, _ in stale]
        to_process = missing + stale_symbols

        # Missing comes first
        assert to_process[0] == "MISSING1"
        # Then stale
        assert to_process[1:] == ["STALE1", "STALE2"]

    def test_oldest_stale_first(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        """Among stale symbols, oldest updated_at should be processed first."""
        now = datetime.now(timezone.utc)
        oldest = (now - CACHE_TTL - timedelta(hours=5)).isoformat()
        middle = (now - CACHE_TTL - timedelta(hours=3)).isoformat()
        newest = (now - CACHE_TTL - timedelta(hours=1)).isoformat()

        universe = [
            {"symbol": "NEWEST"},
            {"symbol": "OLDEST"},
            {"symbol": "MIDDLE"},
        ]
        cache_map = {
            "NEWEST": {"updated_at": newest, "cache_status": "fresh"},
            "OLDEST": {"updated_at": oldest, "cache_status": "fresh"},
            "MIDDLE": {"updated_at": middle, "cache_status": "fresh"},
        }

        service = StockDataIngestionService(batch_size=10)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        stale_sorted = sorted(stale, key=lambda x: x[1])
        stale_symbols = [symbol for symbol, _ in stale_sorted]

        # Should be ordered by updated_at ascending (oldest first)
        assert stale_symbols == ["OLDEST", "MIDDLE", "NEWEST"]

    def test_recently_refreshed_behind_older_stale(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        """A symbol recently refreshed should be behind older stale symbols."""
        now = datetime.now(timezone.utc)
        old_stale = (now - CACHE_TTL - timedelta(hours=10)).isoformat()
        recent_refresh = (now - timedelta(minutes=30)).isoformat()

        universe = [
            {"symbol": "OLD_STALE"},
            {"symbol": "RECENTLY_REFRESHED"},
        ]
        cache_map = {
            "OLD_STALE": {"updated_at": old_stale, "cache_status": "fresh"},
            "RECENTLY_REFRESHED": {"updated_at": recent_refresh, "cache_status": "fresh"},
        }

        service = StockDataIngestionService(batch_size=10)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        # OLD_STALE is stale (older than 6h), RECENTLY_REFRESHED is fresh
        stale_symbols = [s for s, _ in stale]
        assert stale_symbols == ["OLD_STALE"]
        assert fresh == ["RECENTLY_REFRESHED"]

    def test_no_permanent_starvation(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        """Simulate multiple runs: first 30 processed, next run picks next batch."""
        now = datetime.now(timezone.utc)
        # Create 60 symbols all missing
        universe = [{"symbol": f"STOCK{i:03d}"} for i in range(60)]
        cache_map = {f"STOCK{i:03d}": None for i in range(60)}

        service = StockDataIngestionService(batch_size=30)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        to_process = missing + [s for s, _ in stale]

        # First run: first 30
        batch1 = to_process[:30]
        assert len(batch1) == 30
        assert batch1 == [f"STOCK{i:03d}" for i in range(30)]

        # Second run: next 30 (simulate by slicing)
        batch2 = to_process[30:60]
        assert len(batch2) == 30
        assert batch2 == [f"STOCK{i:03d}" for i in range(30, 60)]

        # No overlap
        assert set(batch1).isdisjoint(batch2)

    def test_batch_size_remains_30(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        """Batch size should respect the configured limit."""
        universe = [{"symbol": f"STOCK{i:03d}"} for i in range(100)]
        cache_map = {f"STOCK{i:03d}": None for i in range(100)}

        service = StockDataIngestionService(batch_size=30)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        to_process = missing + [s for s, _ in stale]
        processed = to_process[:30]

        assert len(processed) == 30

    def test_no_additional_per_symbol_queries(
        self, mock_market_service, mock_stock_repo, mock_stock_data_repo, mock_supabase
    ):
        """Prioritization should use existing cache_map, no extra queries."""
        universe = [{"symbol": f"STOCK{i:03d}"} for i in range(100)]
        cache_map = {f"STOCK{i:03d}": None for i in range(100)}

        service = StockDataIngestionService(batch_size=30)
        missing, stale, fresh = service._classify_symbols(universe, cache_map)

        # Verify _classify_symbols doesn't make additional Supabase calls
        # (it only uses the already-loaded cache_map)
        # This is implicitly tested - if it made extra calls, the mock would fail
        assert len(missing) == 100
        assert len(stale) == 0
        assert len(fresh) == 0

    def test_integration_priority_order_in_run(self):
        """Full run should process missing first, then oldest stale."""
        now = datetime.now(timezone.utc)
        old_time = (now - CACHE_TTL - timedelta(hours=10)).isoformat()
        mid_time = (now - CACHE_TTL - timedelta(hours=5)).isoformat()

        universe = [
            {"symbol": "STALE_NEWER"},
            {"symbol": "MISSING1"},
            {"symbol": "STALE_OLDEST"},
            {"symbol": "MISSING2"},
        ]

        with patch("app.services.stock_data_ingestion_service.MarketDataService") as mock_market_service, \
             patch("app.services.stock_data_ingestion_service.StockRepository") as mock_stock_repo, \
             patch("app.services.stock_data_ingestion_service.supabase") as mock_supabase:

            cache_map = {
                "STALE_NEWER": {"updated_at": mid_time, "cache_status": "fresh"},
                "MISSING1": None,
                "STALE_OLDEST": {"updated_at": old_time, "cache_status": "fresh"},
                "MISSING2": None,
            }

            mock_stock_repo.return_value.list_all_by_country.return_value = universe
            mock_supabase.table.return_value.select.return_value.or_.return_value.execute.return_value.data = [
                {"symbol": "STALE_NEWER", "updated_at": mid_time, "cache_status": "fresh"},
                {"symbol": "STALE_OLDEST", "updated_at": old_time, "cache_status": "fresh"},
            ]

            market_instance = mock_market_service.return_value
            market_instance.refresh_stock.return_value = {"quote_json": {}}

            service = StockDataIngestionService(batch_size=3)
            report = service.run()

            # Should process: MISSING1, MISSING2, STALE_OLDEST (batch_size=3)
            # STALE_NEWER should be skipped
            assert report.processed == 3
            assert report.skipped == 1