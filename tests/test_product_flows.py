import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.dependencies import get_optional_authenticated_user
from app.models import AlphaPerformanceSummaryResponse
from app.routers import (
    alpha,
    daily_top_picks,
    financial_history,
    financials,
    historical,
    metrics,
    quote,
    radar,
    score,
    search,
    subscription,
)
from app.services.alpha_portfolio_service import AlphaPortfolioService
from app.services.daily_top_picks_service import DailyTopPicksService
from app.services.performance_service import PerformanceService
from app.services.yahoo_service import YahooService


def _request(headers=None):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/daily-top-picks/india",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            ],
        }
    )


class TestTopPicksAccess:
    @staticmethod
    def _client():
        application = FastAPI()
        application.include_router(daily_top_picks.router)
        return TestClient(application)

    @staticmethod
    def _rows(count):
        return [{"symbol": f"S{index}"} for index in range(count)]

    def test_fastapi_anonymous_request_returns_at_most_ten(self):
        with patch.object(
            daily_top_picks.service,
            "get_country",
            side_effect=lambda country, limit: self._rows(50)[:limit],
        ):
            response = self._client().get("/daily-top-picks/india")

        assert response.status_code == 200
        assert len(response.json()) == 10


class TestAuthenticatedProductRoutes:
    @staticmethod
    def _client():
        application = FastAPI()
        for route in (
            daily_top_picks.router,
            search.router,
            quote.router,
            financials.router,
            financial_history.router,
            historical.router,
            metrics.router,
            score.router,
            radar.router,
            alpha.router,
            subscription.router,
        ):
            application.include_router(route)
        return TestClient(application)

    @staticmethod
    def _rows(count):
        return [{"symbol": f"S{index}"} for index in range(count)]

    @pytest.mark.parametrize(
        "path",
        [
            "/daily-top-picks/generate",
            "/search?q=abc",
            "/quote/ABC",
            "/metrics/ABC",
            "/financials/ABC",
            "/financial-history/ABC",
            "/historical/ABC",
            "/score/ABC",
            "/radar/stocks",
            "/alpha/india",
            "/alpha/history",
            "/alpha/performance",
            "/subscription/user-1",
        ],
    )
    def test_anonymous_product_routes_require_authentication(self, path):
        response = self._client().get(path)
        if path == "/daily-top-picks/generate":
            response = self._client().post(path)
        assert response.status_code == 401

    def test_authenticated_search_reaches_handler(self):
        user = SimpleNamespace(id="user-1", email="user@example.com")
        with patch(
            "app.dependencies.supabase.auth.get_user",
            return_value=SimpleNamespace(user=user),
        ):
            with patch.object(search.stock_repo, "search", return_value=[]):
                response = self._client().get(
                    "/search?q=abc",
                    headers={"Authorization": "Bearer valid-token"},
                )

        assert response.status_code == 200

    def test_authenticated_alpha_reaches_handler(self):
        user = SimpleNamespace(id="user-1", email="user@example.com")
        with patch(
            "app.dependencies.supabase.auth.get_user",
            return_value=SimpleNamespace(user=user),
        ):
            with patch.object(alpha.alpha_repo, "get_all", return_value=[]):
                response = self._client().get(
                    "/alpha/india",
                    headers={"Authorization": "Bearer valid-token"},
                )

        assert response.status_code == 200
        assert response.json() == []

    def test_authenticated_user_id_cannot_access_other_subscription(self):
        user = SimpleNamespace(id="user-1", email="user@example.com")
        with patch(
            "app.dependencies.supabase.auth.get_user",
            return_value=SimpleNamespace(user=user),
        ):
            response = self._client().get(
                "/subscription/another-user",
                headers={"Authorization": "Bearer valid-token"},
            )

        assert response.status_code == 403

    def test_fastapi_valid_bearer_returns_at_most_fifty(self):
        auth_user = SimpleNamespace(id="user-1", email="user@example.com")
        with patch(
            "app.dependencies.supabase.auth.get_user",
            return_value=SimpleNamespace(user=auth_user),
        ) as get_user:
            with patch.object(
                daily_top_picks.service,
                "get_country",
                side_effect=lambda country, limit: self._rows(50)[:limit],
            ):
                response = self._client().get(
                    "/daily-top-picks/india",
                    headers={"Authorization": "Bearer valid-token"},
                )

        assert response.status_code == 200
        assert len(response.json()) == 50
        get_user.assert_called_once_with("valid-token")

    @pytest.mark.parametrize("token", ["invalid-token", "expired-token"])
    def test_fastapi_invalid_or_expired_bearer_is_rejected(self, token):
        with patch(
            "app.dependencies.supabase.auth.get_user",
            side_effect=Exception("invalid token"),
        ):
            response = self._client().get(
                "/daily-top-picks/india",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 401

    def test_fastapi_user_id_query_cannot_bypass_anonymous_limit(self):
        with patch.object(
            daily_top_picks.service,
            "get_country",
            side_effect=lambda country, limit: self._rows(50)[:limit],
        ):
            response = self._client().get(
                "/daily-top-picks/india?user_id=somebody-else"
            )

        assert response.status_code == 200
        assert len(response.json()) == 10

    def test_fastapi_limit_query_cannot_bypass_anonymous_limit(self):
        with patch.object(
            daily_top_picks.service,
            "get_country",
            side_effect=lambda country, limit: self._rows(50)[:limit],
        ):
            response = self._client().get("/daily-top-picks/india?limit=1000")

        assert response.status_code == 200
        assert len(response.json()) == 10

    def test_anonymous_top_picks_are_limited_to_ten(self):
        with patch.object(daily_top_picks.service, "get_country", return_value=[]) as get_country:
            daily_top_picks.india(user=None)

        get_country.assert_called_once_with("IN", limit=10)

    def test_generation_persists_fifty_ranked_records(self):
        service = DailyTopPicksService.__new__(DailyTopPicksService)
        service.stock_repo = MagicMock()
        service.stock_data_repo = MagicMock()
        service.market = MagicMock()
        service.alpha_filter = MagicMock()
        service.repository = MagicMock()
        service.logger = MagicMock()
        service.stock_repo.list_all_by_country.return_value = [
            {"symbol": f"S{index:02d}", "company_name": "Company", "exchange": "NSE"}
            for index in range(50)
        ]
        service.stock_repo.get_many_by_symbol_for_top_picks.return_value = [
            {"symbol": f"S{index:02d}", "exchange": "NSE"}
            for index in range(50)
        ]
        service.stock_data_repo.get_many_for_top_picks.return_value = []
        service.market._is_cache_valid.return_value = False
        service.market.refresh_stock.side_effect = [
            {
                "eligibility_json": {"financials_complete": True},
                "score_json": {
                    "overall_score": float(50 - index),
                    "growth_score": 1,
                    "quality_score": 1,
                    "financial_strength_score": 1,
                    "valuation_score": 1,
                },
            }
            for index in range(50)
        ]
        service.alpha_filter.is_eligible.return_value = (True, "Eligible")

        # The refresh budget is raised for this test so it keeps covering the
        # original contract: all 50 candidates are ranked and persisted. The
        # bounded production default is covered separately in
        # tests/test_top_picks_egress.py.
        rows = service.generate_country("IN", refresh_budget=50, quote_refresh_budget=0)

        assert len(rows) == 50
        assert rows[0]["overall_score"] == 50.0
        assert rows[-1]["overall_score"] == 1.0
        service.repository.replace_country.assert_called_once()
        assert len(service.repository.replace_country.call_args.kwargs["rows"]) == 50

    def test_authenticated_top_picks_are_limited_to_fifty(self):
        with patch.object(daily_top_picks.service, "get_country", return_value=[]) as get_country:
            daily_top_picks.usa(user={"id": "user-1"})

        get_country.assert_called_once_with("US", limit=50)

    def test_invalid_bearer_token_is_rejected(self):
        with patch("app.dependencies.supabase.auth.get_user", side_effect=Exception("invalid")):
            with pytest.raises(HTTPException) as error:
                get_optional_authenticated_user(
                    _request({"Authorization": "Bearer invalid-token"})
                )

        assert error.value.status_code == 401

    def test_user_id_parameter_does_not_control_access(self):
        with patch.object(daily_top_picks.service, "get_country", return_value=[]) as get_country:
            daily_top_picks.india(user=None)

        get_country.assert_called_once_with("IN", limit=10)


class TestAlphaPortfolio:
    def test_new_holding_uses_provider_price_and_entry_timestamp(self):
        service = AlphaPortfolioService.__new__(AlphaPortfolioService)
        service.daily_repo = MagicMock()
        service.alpha_repo = MagicMock()
        service.history_repo = MagicMock()
        service.market = MagicMock()
        service.stock_data_repo = MagicMock()
        service.logger = MagicMock()
        service.alpha_repo.get_all.return_value = []
        service.alpha_repo.insert_one.return_value = {"symbol": "ABC"}
        service.daily_repo.get_country.return_value = [
            {"symbol": "ABC", "company_name": "ABC Co", "exchange": "NSE", "overall_score": 90, "rank": 1}
        ]
        service.stock_data_repo.get_quote_data.return_value = None
        service.market.get_stock.return_value = {
            "quote_json": {"current_price": 123.45}
        }

        count = service.reconcile_market("IN")

        assert count == 1
        stored_row = service.alpha_repo.insert_one.call_args.args[0]
        assert stored_row["entry_price"] == 123.45
        assert stored_row["entry_date"]
        service.history_repo.insert_event.assert_called_once_with(
            market="IN",
            symbol="ABC",
            action=service.history_repo.ACTION_ADD,
            reason="Added from Daily Top Picks",
            price=123.45,
            score=90,
            company_name="ABC Co",
        )

    def test_retained_holding_is_not_rewritten(self):
        service = AlphaPortfolioService.__new__(AlphaPortfolioService)
        service.daily_repo = MagicMock()
        service.alpha_repo = MagicMock()
        service.history_repo = MagicMock()
        service.market = MagicMock()
        service.logger = MagicMock()
        service.alpha_repo.get_all.return_value = [
            {
                "symbol": "ABC",
                "entry_price": 123.45,
                "entry_date": "2026-01-01T00:00:00+00:00",
            }
        ]
        service.daily_repo.get_country.return_value = [
            {"symbol": "ABC", "company_name": "ABC Co", "exchange": "NSE", "overall_score": 90, "rank": 1}
        ]

        assert service.reconcile_market("IN") == 1
        service.alpha_repo.insert_one.assert_not_called()
        service.alpha_repo.delete.assert_not_called()
        service.market.get_stock.assert_not_called()
        service.history_repo.insert_event.assert_not_called()

    def test_removed_holding_is_deleted_without_bulk_replace(self):
        service = AlphaPortfolioService.__new__(AlphaPortfolioService)
        service.daily_repo = MagicMock()
        service.alpha_repo = MagicMock()
        service.history_repo = MagicMock()
        service.market = MagicMock()
        service.logger = MagicMock()
        service.alpha_repo.get_all.return_value = [
            {
                "symbol": "ABC",
                "entry_price": 123.45,
                "entry_date": "2026-01-01T00:00:00+00:00",
                "overall_score": 90,
                "company_name": "ABC Co",
            }
        ]
        service.daily_repo.get_country.return_value = []

        assert service.reconcile_market("IN") == 0
        service.alpha_repo.delete.assert_called_once_with("ABC", market="IN")
        service.alpha_repo.insert_one.assert_not_called()
        service.history_repo.insert_event.assert_called_once_with(
            market="IN",
            symbol="ABC",
            action=service.history_repo.ACTION_REMOVE,
            reason="Removed from Daily Top Picks",
            price=None,
            score=90,
            company_name="ABC Co",
        )

    def test_new_holding_without_provider_price_is_skipped(self):
        service = AlphaPortfolioService.__new__(AlphaPortfolioService)
        service.daily_repo = MagicMock()
        service.alpha_repo = MagicMock()
        service.history_repo = MagicMock()
        service.market = MagicMock()
        service.logger = MagicMock()
        service.alpha_repo.get_all.return_value = []
        service.daily_repo.get_country.return_value = [
            {"symbol": "ABC", "overall_score": 90, "rank": 1}
        ]
        service.market.get_stock.side_effect = RuntimeError("provider unavailable")

        assert service.reconcile_market("IN") == 0
        service.alpha_repo.insert_one.assert_not_called()
        service.history_repo.insert_event.assert_not_called()

    def test_performance_snapshot_is_egress_neutral_and_steps_nav_from_cache(self):
        """Snapshot NAV must come from the ingestion cache only.

        This test previously asserted that a Yahoo historical download was
        made for the benchmark. Ingestion already covers every Alpha
        holding, so the snapshot reads `stock_data.quote_json` and makes no
        external market-data call at all.
        """
        service = PerformanceService.__new__(PerformanceService)
        service.alpha_repo = MagicMock()
        service.performance_repo = MagicMock()
        service.alpha_history_repo = MagicMock()
        service.stock_data_repo = MagicMock()
        service.alpha_history_repo.get_events_up_to.return_value = [
            {"symbol": "ABC", "action": "ADD"},
            {"symbol": "DEF", "action": "ADD"},
        ]
        service.alpha_repo.get_all.return_value = [
            {
                "symbol": "ABC",
                "entry_price": 100.0,
                "entry_date": "2026-01-01T00:00:00+00:00",
            },
            {
                "symbol": "DEF",
                "entry_price": 200.0,
                "entry_date": "2026-01-01T00:00:00+00:00",
            },
        ]
        service.performance_repo.get_latest.return_value = {
            "portfolio_nav": 100.0,
            "benchmark_nav": 100.0,
        }
        service.stock_data_repo.get_quote_data_many.return_value = [
            {
                "symbol": "ABC",
                "quote_json": {"current_price": 110.0, "previous_close": 100.0},
                "updated_at": datetime.utcnow().isoformat(),
            },
            {
                "symbol": "DEF",
                "quote_json": {"current_price": 220.0, "previous_close": 200.0},
                "updated_at": datetime.utcnow().isoformat(),
            },
        ]

        # Any provider attribute access is a hard failure.
        service.market = SimpleNamespace(
            __getattr__=lambda *_: pytest.fail("external call attempted")
        )

        result = service.snapshot_market("IN")

        assert result["holdings_count"] == 2
        # ABC +10% and DEF +10% average to +10%, so NAV steps 100 -> 110.
        assert result["portfolio_nav"] == pytest.approx(110.0)
        assert result["date"]
        assert result["period"] == "INCEPTION"
        service.performance_repo.insert_snapshot.assert_called_once()

        payload = service.performance_repo.insert_snapshot.call_args[0][0]
        for field in ("date", "as_of", "period", "holdings_count"):
            assert field in payload

    def test_performance_never_touches_a_market_data_provider(self):
        service = PerformanceService.__new__(PerformanceService)
        assert not hasattr(service, "market")

        # The service module must not import or reference a provider.
        import app.services.performance_service as perf_module

        module_source = Path(perf_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "self.market",
            "get_quote(",
            "get_historical_prices",
            "get_financials",
            "MarketDataService",
            "YahooService",
            "YahooProvider",
            "yahoo",
        ):
            assert forbidden not in module_source, (
                "performance_service.py must not reference %s" % forbidden
            )


class TestAlphaPerformanceApi:
    """F/G/H: the /alpha/performance contract consumed by the Alpha page."""

    def _get(self, path, report, latest=None):
        """Issue a request with the canonical calculation stubbed out.

        The stub must stay active for the duration of the request, so the
        client is exercised inside the patch context.
        """
        app = FastAPI()
        app.include_router(alpha.router)

        fake_perf_repo = MagicMock()
        fake_perf_repo.get_latest.return_value = latest

        with patch.object(alpha, "perf_repo", fake_perf_repo), patch.object(
            alpha.PerformanceService,
            "report_performance",
            return_value=report,
        ):
            return TestClient(app).get(path)

    def test_returns_frontend_contract_fields(self):
        response = self._get(
            "/alpha/performance?market=IN",
            {
                "market": "IN",
                "period": "since-inception",
                "status": "available",
                "benchmark": "^NSEI",
                "portfolio_return": 0.12,
                "benchmark_return": 0.05,
                "excess_return": 0.07,
                "as_of": "2026-09-21T00:00:00",
                "holdings_count": 15,
                "reason": None,
            },
            latest={
                "date": "2026-09-21",
                "as_of": "2026-09-21T00:00:00",
                "portfolio_nav": 112.0,
                "benchmark_nav": 105.0,
                "holdings_count": 15,
            },
        )

        assert response.status_code == 200
        body = response.json()
        for field in (
            "portfolio_return",
            "benchmark_return",
            "alpha",
            "portfolio_nav",
            "benchmark_nav",
            "as_of",
            "date",
            "holdings_count",
        ):
            assert field in body, "response is missing %s" % field

        # Percent scale, because the page appends "%" without rescaling.
        assert body["portfolio_return"] == pytest.approx(12.0)
        assert body["benchmark_return"] == pytest.approx(5.0)
        assert body["portfolio_nav"] == pytest.approx(112.0)
        assert body["date"] == "2026-09-21"
        assert body["holdings_count"] == 15

    def test_alpha_equals_portfolio_minus_benchmark(self):
        response = self._get(
            "/alpha/performance?market=IN",
            {
                "market": "IN",
                "period": "since-inception",
                "status": "available",
                "benchmark": "^NSEI",
                "portfolio_return": -0.08,
                "benchmark_return": 0.03,
                "excess_return": -0.11,
                "as_of": "2026-09-21T00:00:00",
                "holdings_count": 15,
                "reason": None,
            }
        )

        body = response.json()

        assert body["alpha"] == pytest.approx(
            body["portfolio_return"] - body["benchmark_return"]
        )
        assert body["alpha"] == pytest.approx(-11.0)

    def test_insufficient_history_returns_explicit_empty_state(self):
        response = self._get(
            "/alpha/performance?market=IN",
            {
                "market": "IN",
                "period": "since-inception",
                "status": "not_available",
                "benchmark": "^NSEI",
                "portfolio_return": None,
                "benchmark_return": None,
                "excess_return": None,
                "as_of": "2026-09-21T00:00:00",
                "holdings_count": 0,
                "reason": "Insufficient historical performance data for this period",
            }
        )

        body = response.json()

        assert body["status"] == "not_available"
        assert body["portfolio_return"] is None
        assert body["benchmark_return"] is None
        assert body["alpha"] is None
        assert "Insufficient" in body["reason"]

    def test_alpha_is_null_when_benchmark_is_null(self):
        response = self._get(
            "/alpha/performance?market=IN",
            {
                "market": "IN",
                "period": "since-inception",
                "status": "available",
                "benchmark": "^NSEI",
                "portfolio_return": 0.12,
                "benchmark_return": None,
                "excess_return": None,
                "as_of": "2026-09-21T00:00:00",
                "holdings_count": 15,
                "reason": None,
            }
        )

        body = response.json()

        assert body["portfolio_return"] == pytest.approx(12.0)
        assert body["benchmark_return"] is None
        assert body["alpha"] is None

    def test_response_matches_summary_model(self):
        response = self._get(
            "/alpha/performance?market=IN",
            {
                "market": "IN",
                "period": "since-inception",
                "status": "available",
                "benchmark": "^NSEI",
                "portfolio_return": 0.12,
                "benchmark_return": 0.05,
                "excess_return": 0.07,
                "as_of": "2026-09-21T00:00:00",
                "holdings_count": 15,
                "reason": None,
            }
        )

        body = response.json()

        model = AlphaPerformanceSummaryResponse(**body)
        assert model.market == "IN"
        assert model.alpha == pytest.approx(7.0)

    def test_market_omitted_returns_both_markets(self):
        response = self._get(
            "/alpha/performance",
            {
                "market": "IN",
                "period": "since-inception",
                "status": "available",
                "benchmark": "^NSEI",
                "portfolio_return": 0.12,
                "benchmark_return": 0.05,
                "excess_return": 0.07,
                "as_of": "2026-09-21T00:00:00",
                "holdings_count": 15,
                "reason": None,
            }
        )

        body = response.json()

        assert set(body) == {"india", "usa"}
        assert body["india"]["market"] == "IN"
        assert body["usa"]["market"] == "US"
        for key in ("india", "usa"):
            assert "portfolio_return" in body[key]


class TestDataCoverage:
    def test_metrics_resolves_exchange_suffix(self):
        metrics.stock_repo.get_by_symbol = MagicMock(
            return_value={"exchange": "NSE"}
        )

        assert metrics._resolve_symbol("RELIANCE") == "RELIANCE.NS"

    def test_yahoo_retries_transient_failures_with_a_bound(self):
        service = YahooService()
        operation = MagicMock(
            side_effect=[TimeoutError("timeout"), TimeoutError("timeout"), "ok"]
        )

        with patch("app.services.yahoo_service.time.sleep") as sleep:
            assert service._with_transient_retries(operation) == "ok"

        assert operation.call_count == 3
        assert sleep.call_count == 2

    @pytest.mark.parametrize(
        "message",
        [
            "getaddrinfo failed",
            "temporary failure in name resolution",
            "connection reset by peer",
            "429 too many requests",
            "503 service unavailable",
        ],
    )
    def test_yahoo_classifies_common_transient_errors(self, message):
        assert YahooService._is_transient_provider_error(Exception(message))

    def test_yahoo_does_not_classify_invalid_symbol_as_transient(self):
        assert not YahooService._is_transient_provider_error(
            Exception("possibly delisted: no price data found")
        )
