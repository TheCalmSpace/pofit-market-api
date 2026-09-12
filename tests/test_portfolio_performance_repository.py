from unittest.mock import MagicMock, patch

from app.repositories.portfolio_performance_repository import PortfolioPerformanceRepository


class TestPortfolioPerformanceRepository:
    @patch("app.repositories.portfolio_performance_repository.supabase")
    def test_insert_snapshot_normalizes_market_and_supplies_as_of(self, mock_supabase):
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": 1, "market": "IN"}
        ]
        repository = PortfolioPerformanceRepository()

        result = repository.insert_snapshot(
            {
                "market": "in",
                "portfolio_return": 0.1,
                "benchmark_return": 0.05,
                "alpha": 0.05,
                "holdings_count": 2,
            }
        )

        assert result == {"id": 1, "market": "IN"}
        payload = mock_supabase.table.return_value.insert.call_args.args[0]
        assert payload["market"] == "IN"
        assert payload["as_of"]
        assert payload["portfolio_return"] == 0.1

    @patch("app.repositories.portfolio_performance_repository.supabase")
    def test_get_latest_orders_by_as_of(self, mock_supabase):
        table = mock_supabase.table.return_value
        select = table.select.return_value
        eq = select.eq.return_value
        order = eq.order.return_value
        limit = order.limit.return_value
        limit.execute.return_value.data = [{"market": "IN", "as_of": "2026-09-12T00:00:00+00:00"}]
        repository = PortfolioPerformanceRepository()

        result = repository.get_latest("in")

        assert result["market"] == "IN"
        table.select.assert_called_once_with("*")
        table.select.return_value.eq.assert_called_once_with("market", "IN")
        table.select.return_value.eq.return_value.order.assert_called_once_with("as_of", desc=True)
        table.select.return_value.eq.return_value.order.return_value.limit.assert_called_once_with(1)

    @patch("app.repositories.portfolio_performance_repository.supabase")
    def test_get_history_orders_by_as_of(self, mock_supabase):
        table = mock_supabase.table.return_value
        select = table.select.return_value
        eq = select.eq.return_value
        order = eq.order.return_value
        limit = order.limit.return_value
        limit.execute.return_value.data = []
        repository = PortfolioPerformanceRepository()

        result = repository.get_history("US", limit=10)

        assert result == []
        table.select.return_value.eq.assert_called_once_with("market", "US")
        table.select.return_value.eq.return_value.order.assert_called_once_with("as_of", desc=True)
        table.select.return_value.eq.return_value.order.return_value.limit.assert_called_once_with(10)
