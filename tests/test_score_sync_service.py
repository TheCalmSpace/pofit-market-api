from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.score_sync_service import ScoreSyncService
from app.services.score_service import ScoreService


def _cached_score(score=None, updated_at=None):
    return {
        "symbol": "ABC",
        "score_json": score,
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "quote_json": {"current_price": 100.0},
        "metrics_json": {
            "growth": {"revenue_cagr_3y": 10},
            "quality": {"average_roe": 0.2},
            "financial_strength": {"cash_to_debt": 1.0},
            "valuation": {"pe": 20.0},
        },
    }


def _score(**overrides):
    value = {
        "overall_score": 81.5,
        "grade": "A",
        "model_version": "v1",
        "calculated_at": "2026-09-10T08:00:00+00:00",
        "data_as_of": "2026-09-10T08:00:00+00:00",
        "data_quality_status": "fresh",
        "growth_score": 80.0,
        "quality_score": 82.0,
        "valuation_score": 84.0,
        "financial_strength_score": 81.0,
    }
    value.update(overrides)
    return value


def _service(main_client, personal_client):
    service = ScoreSyncService.__new__(ScoreSyncService)
    service.main_supabase = main_client
    service.personal_supabase = personal_client
    return service


class TestScoreSyncService:
    def test_workflow_import(self):
        from app.services.score_sync_service import ScoreSyncService as Imported

        assert Imported is ScoreSyncService

    def test_valid_mapping_and_idempotent_conflict_key(self):
        main = MagicMock()
        personal = MagicMock()
        main.table.return_value.select.return_value.execute.return_value.data = [
            _cached_score(_score())
        ]
        personal.table.return_value.select.return_value.execute.return_value.data = [
            {"id": "personal-stock-id", "symbol": "ABC", "exchange": "NSE"}
        ]
        personal.table.return_value.upsert.return_value.execute.return_value.data = []
        service = _service(main, personal)

        with patch.object(service, "_fetch_main_scores", wraps=service._fetch_main_scores), patch.object(
            service, "_fetch_personal_stocks", wraps=service._fetch_personal_stocks
        ):
            report = service.run()

        assert report.synced == 1
        payload = personal.table.return_value.upsert.call_args.args[0]
        assert payload["stock_id"] == "personal-stock-id"
        assert payload["total_score"] == 81.5
        assert payload["grade"] == "A"
        assert payload["model_version"] == "v1"
        assert payload["growth_score"] == 80.0
        assert payload["quality_score"] == 82.0
        assert payload["valuation_score"] == 84.0
        assert payload["financial_strength_score"] == 81.0
        assert payload["momentum_score"] is None
        assert payload["risk_level"] is None
        assert personal.table.return_value.upsert.call_args.kwargs["on_conflict"] == (
            "stock_id,calculated_at"
        )

    def test_missing_model_version_uses_main_score_model_version(self):
        main = MagicMock()
        personal = MagicMock()
        main.table.return_value.select.return_value.execute.return_value.data = [
            _cached_score(_score(model_version=None))
        ]
        personal.table.return_value.select.return_value.execute.return_value.data = [
            {"id": "personal-stock-id", "symbol": "ABC", "exchange": "NSE"}
        ]
        personal.table.return_value.upsert.return_value.execute.return_value.data = []
        service = _service(main, personal)

        report = service.run()

        assert report.synced == 1
        payload = personal.table.return_value.upsert.call_args.args[0]
        assert payload["model_version"] == ScoreService.MODEL_VERSION

    def test_missing_score_json_is_skipped(self):
        main = MagicMock()
        personal = MagicMock()
        main.table.return_value.select.return_value.execute.return_value.data = [
            _cached_score(None)
        ]
        personal.table.return_value.select.return_value.execute.return_value.data = [
            {"id": "personal-stock-id", "symbol": "ABC", "exchange": "NSE"}
        ]
        service = _service(main, personal)

        report = service.run()

        assert report.skipped == 1
        personal.table.return_value.upsert.assert_not_called()

    def test_incomplete_score_is_skipped_without_fake_values(self):
        main = MagicMock()
        personal = MagicMock()
        main.table.return_value.select.return_value.execute.return_value.data = [
            _cached_score(_score(quality_score=None))
        ]
        personal.table.return_value.select.return_value.execute.return_value.data = [
            {"id": "personal-stock-id", "symbol": "ABC", "exchange": "NSE"}
        ]
        service = _service(main, personal)

        report = service.run()

        assert report.skipped == 1
        personal.table.return_value.upsert.assert_not_called()

    def test_invalid_data_quality_score_is_skipped(self):
        main = MagicMock()
        personal = MagicMock()
        main.table.return_value.select.return_value.execute.return_value.data = [
            _cached_score(_score(data_quality_status="invalid"))
        ]
        personal.table.return_value.select.return_value.execute.return_value.data = [
            {"id": "personal-stock-id", "symbol": "ABC", "exchange": "NSE"}
        ]
        service = _service(main, personal)

        report = service.run()

        assert report.skipped == 1
        personal.table.return_value.upsert.assert_not_called()

    def test_stale_score_is_marked_stale(self):
        main = MagicMock()
        personal = MagicMock()
        old = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        main.table.return_value.select.return_value.execute.return_value.data = [
            _cached_score(_score(), updated_at=old)
        ]
        personal.table.return_value.select.return_value.execute.return_value.data = [
            {"id": "personal-stock-id", "symbol": "ABC", "exchange": "NSE"}
        ]
        personal.table.return_value.upsert.return_value.execute.return_value.data = []
        service = _service(main, personal)

        report = service.run()

        assert report.synced == 1
        payload = personal.table.return_value.upsert.call_args.args[0]
        assert payload["data_quality_status"] == "stale"

    def test_personal_stock_mapping_skips_unknown_symbol(self):
        main = MagicMock()
        personal = MagicMock()
        main.table.return_value.select.return_value.execute.return_value.data = [
            _cached_score(_score())
        ]
        personal.table.return_value.select.return_value.execute.return_value.data = []
        service = _service(main, personal)

        report = service.run()

        assert report.skipped == 1
        personal.table.return_value.upsert.assert_not_called()

    def test_main_and_personal_clients_are_separate(self):
        main = MagicMock(name="main_supabase")
        personal = MagicMock(name="personal_supabase")
        main.table.return_value.select.return_value.execute.return_value.data = []
        personal.table.return_value.select.return_value.execute.return_value.data = []
        service = _service(main, personal)

        service.run()

        main.table.assert_called_once_with("stock_data")
        personal.table.assert_called_once_with("stocks")
        assert service.personal_supabase is personal
        assert main is not personal
