from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.data_quality import DataQualityAssessor
from app.utils.symbol import resolve_yahoo_symbol

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def mock_supabase_auth(monkeypatch):
    monkeypatch.setattr(
        "app.dependencies.supabase.auth.get_user",
        lambda token: SimpleNamespace(
            user=SimpleNamespace(id="test-user", email="test@example.com")
        ),
    )


class TestSymbolResolution:
    def test_nse_symbol_gets_dot_ns(self):
        assert resolve_yahoo_symbol("RELIANCE", "NSE") == "RELIANCE.NS"
        assert resolve_yahoo_symbol("TCS", "NSE") == "TCS.NS"

    def test_bse_symbol_gets_dot_bo(self):
        assert resolve_yahoo_symbol("RELIANCE", "BSE") == "RELIANCE.BO"

    def test_already_dotted_symbol_unchanged(self):
        assert resolve_yahoo_symbol("RELIANCE.NS") == "RELIANCE.NS"

    def test_unknown_exchange_unchanged(self):
        assert resolve_yahoo_symbol("RELIANCE", "NASDAQ") == "RELIANCE"


class TestDataQualityAssessor:
    def test_assess_quote_valid(self):
        cache = {
            "quote_json": {"current_price": 2500.0, "currency": "INR"},
            "updated_at": "2026-01-01T00:00:00Z",
        }
        result = DataQualityAssessor.assess_quote(cache)
        assert result["status"] == "stale"

    def test_assess_quote_missing(self):
        result = DataQualityAssessor.assess_quote(None)
        assert result["status"] == "insufficient"

    def test_assess_quote_invalid_price(self):
        cache = {
            "quote_json": {"current_price": -10, "currency": "INR"},
            "updated_at": "2026-01-01T00:00:00Z",
        }
        result = DataQualityAssessor.assess_quote(cache)
        assert result["status"] == "invalid"

    def test_assess_financials_partial(self):
        cache = {
            "metrics_json": {"growth": None, "quality": None},
            "updated_at": "2026-01-01T00:00:00Z",
        }
        result = DataQualityAssessor.assess_financials(cache)
        assert result["status"] == "partial"

    def test_assess_score_stale_with_fresh_quote(self):
        cache = {
            "score_json": {"overall_score": 50.0, "model_version": "v1"},
            "quote_json": {"current_price": 2500.0},
            "updated_at": "2000-01-01T00:00:00Z",
        }
        result = DataQualityAssessor.assess_score(cache)
        assert result["status"] == "stale"
        assert result["score"] == 50.0


class TestMetricsCrashFix:
    @patch("app.routers.metrics.YahooService")
    def test_metrics_handles_symbol_not_found(self, mock_yahoo_cls):
        mock_yahoo = MagicMock()
        mock_yahoo.get_financials.side_effect = Exception("SymbolNotFoundError")
        mock_yahoo_cls.return_value = mock_yahoo

        response = client.get("/metrics/RELIANCE", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["data_status"] == "insufficient_data"

    @patch("app.routers.metrics.YahooService")
    def test_metrics_handles_missing_financials(self, mock_yahoo_cls):
        mock_yahoo = MagicMock()
        mock_yahoo.get_financials.return_value = MagicMock(data_status="unavailable")
        mock_yahoo.get_financial_history.return_value = MagicMock(data_status="unavailable")
        mock_yahoo_cls.return_value = mock_yahoo

        response = client.get("/metrics/RELIANCE", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["data_status"] == "insufficient_data"


class TestScoreSafety:
    @patch("app.routers.score.stock_data_repo")
    @patch("app.routers.score.market_service")
    def test_stale_score_returns_insufficient_data(self, mock_market, mock_repo):
        mock_repo.get.return_value = {
            "score_json": {
                "symbol": "RELIANCE.NS",
                "overall_score": 51.84,
                "grade": "D",
                "growth_score": 25.96,
                "quality_score": 76.73,
                "financial_strength_score": 31.04,
                "valuation_score": 88.49,
            },
            "updated_at": "2000-01-01T00:00:00Z",
            "cache_status": "fresh",
            "quote_json": {
                "symbol": "RELIANCE.NS",
                "company_name": "Reliance Industries",
                "current_price": 2500.0,
            },
            "metrics_json": {"growth": {}, "quality": {}, "financial_strength": {}, "valuation": {}},
            "financials_json": {
                "symbol": "RELIANCE.NS",
                "company_name": "Reliance Industries",
                "revenue_ttm": 1000.0,
                "net_income": 100.0,
                "total_equity": 500.0,
                "total_debt": 200.0,
                "total_assets": 1000.0,
                "current_ratio": 1.5,
                "market_cap": 100000.0,
                "trailing_pe": 20.0,
            },
            "financial_history_json": {
                "symbol": "RELIANCE.NS",
                "annual": [
                    {"year": 2023, "revenue": 900.0, "net_income": 90.0, "shareholders_equity": 450.0},
                    {"year": 2022, "revenue": 800.0, "net_income": 80.0, "shareholders_equity": 400.0},
                ],
            },
        }
        mock_market.get_stock.return_value = mock_repo.get.return_value

        response = client.get("/score/RELIANCE", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["data_status"] == "insufficient_data"
        assert data["data_quality_status"] == "REVIEW_REQUIRED"

    @patch("app.routers.score.stock_data_repo")
    @patch("app.routers.score.market_service")
    def test_fresh_score_returns_score(self, mock_market, mock_repo):
        now = datetime.utcnow().isoformat() + "Z"
        mock_repo.get.return_value = {
            "score_json": {
                "symbol": "INFY.NS",
                "overall_score": 58.01,
                "grade": "D",
                "growth_score": 28.37,
                "quality_score": 83.23,
                "financial_strength_score": 57.66,
                "valuation_score": 67.46,
            },
            "updated_at": now,
            "cache_status": "fresh",
            "quote_json": {
                "symbol": "INFY.NS",
                "company_name": "Infosys",
                "current_price": 1500.0,
            },
            "metrics_json": {"growth": {}, "quality": {}, "financial_strength": {}, "valuation": {}},
            "financials_json": {
                "symbol": "INFY.NS",
                "company_name": "Infosys",
                "revenue_ttm": 1000.0,
                "net_income": 100.0,
                "total_equity": 500.0,
                "total_debt": 200.0,
                "total_assets": 1000.0,
                "current_ratio": 1.5,
                "market_cap": 100000.0,
                "trailing_pe": 20.0,
            },
            "financial_history_json": {
                "symbol": "INFY.NS",
                "annual": [
                    {"year": 2023, "revenue": 900.0, "net_income": 90.0, "shareholders_equity": 450.0},
                    {"year": 2022, "revenue": 800.0, "net_income": 80.0, "shareholders_equity": 400.0},
                ],
            },
        }
        mock_market.get_stock.return_value = mock_repo.get.return_value

        response = client.get("/score/INFY", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["data_status"] == "available"
        assert data["overall_score"] == 58.01


class TestBankHandling:
    @patch("app.routers.financials.yahoo_service")
    @patch("app.routers.financials.stock_repo")
    def test_bank_financials_may_return_invalid_symbol(self, mock_repo, mock_yahoo):
        from app.services.yahoo_service import SymbolNotFoundError
        mock_yahoo.get_financials.side_effect = SymbolNotFoundError("HDFCBANK")
        mock_repo.get_by_symbol.return_value = None

        response = client.get("/financials/HDFCBANK", headers=AUTH_HEADERS)
        assert response.status_code == 404
