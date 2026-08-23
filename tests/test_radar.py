import logging
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.stock_repository import StockRepository
from app.routers.radar import get_radar_stocks

logging.basicConfig(level=logging.DEBUG)


def _unwrap_query(value):
    """Unwrap FastAPI Query default objects for test assertions."""
    if hasattr(value, "default"):
        return value.default
    return value


class TestRadarEndpoint:
    @patch("app.routers.radar.stock_repo")
    def test_status_filter(self, mock_repo):
        mock_repo.list_by_status.return_value = [
            {
                "symbol": "NEWCO",
                "company_name": "New Company",
                "exchange": "NSE",
                "isin": "INENEW001",
                "status": "NEWLY_LISTED",
                "first_listed_date": "2024-01-15",
                "last_synced_at": "2024-01-15T10:00:00Z",
            }
        ]

        result = get_radar_stocks(status="NEWLY_LISTED", limit=10)
        call_kwargs = mock_repo.list_by_status.call_args.kwargs
        assert call_kwargs["status"] == "NEWLY_LISTED"
        assert _unwrap_query(call_kwargs["exchange"]) is None
        assert call_kwargs["limit"] == 10
        assert len(result) == 1
        assert result[0]["symbol"] == "NEWCO"
        assert result[0]["status"] == "NEWLY_LISTED"

    @patch("app.routers.radar.stock_repo")
    def test_exchange_filter(self, mock_repo):
        mock_repo.list_by_status.return_value = [
            {
                "symbol": "RELIANCE",
                "company_name": "Reliance Industries",
                "exchange": "NSE",
                "isin": "INE002A01018",
                "status": "ELIGIBLE",
                "first_listed_date": None,
                "last_synced_at": "2024-01-15T10:00:00Z",
            }
        ]

        result = get_radar_stocks(status="ELIGIBLE", exchange="NSE", limit=10)
        call_kwargs = mock_repo.list_by_status.call_args.kwargs
        assert call_kwargs["status"] == "ELIGIBLE"
        assert call_kwargs["exchange"] == "NSE"
        assert call_kwargs["limit"] == 10
        assert len(result) == 1
        assert result[0]["exchange"] == "NSE"

    @patch("app.routers.radar.stock_repo")
    def test_no_status_filter_returns_all(self, mock_repo):
        mock_repo.list_all_by_country.return_value = [
            {
                "symbol": "RELIANCE",
                "company_name": "Reliance Industries",
                "exchange": "NSE",
                "country": "IN",
            }
        ]

        result = get_radar_stocks(limit=10)
        mock_repo.list_all_by_country.assert_called_once_with("IN", limit=10)
        assert len(result) == 1
        assert result[0]["symbol"] == "RELIANCE"

    @patch("app.routers.radar.stock_repo")
    def test_missing_optional_fields(self, mock_repo):
        mock_repo.list_by_status.return_value = [
            {
                "symbol": "PARTIAL",
                "company_name": "Partial Data",
                "exchange": "NSE",
            }
        ]

        result = get_radar_stocks(status="REVIEW_REQUIRED", limit=10)
        assert len(result) == 1
        assert result[0]["isin"] is None
        assert result[0]["first_listed_date"] is None
        assert result[0]["last_synced_at"] is None

    @patch("app.routers.radar.stock_repo")
    def test_empty_result(self, mock_repo):
        mock_repo.list_by_status.return_value = []

        result = get_radar_stocks(status="INACTIVE", limit=10)
        assert result == []

    @patch("app.routers.radar.stock_repo")
    def test_limit_capped(self, mock_repo):
        mock_repo.list_by_status.return_value = []

        get_radar_stocks(status="NEWLY_LISTED", limit=5000)
        call_kwargs = mock_repo.list_by_status.call_args.kwargs
        assert call_kwargs["limit"] == 5000
