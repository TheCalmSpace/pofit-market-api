import json
import logging
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.nse_client import NSEClient
from app.services.personal_sync_service import PersonalSyncService, PersonalSyncReport
from app.services.universe_sync_service import SyncReport, UniverseSyncService

logging.basicConfig(level=logging.DEBUG)


class TestNSEClient:
    def test_normalize_series(self):
        assert NSEClient.normalize_series("eq") == "EQ"
        assert NSEClient.normalize_series("SM") == "SM"
        assert NSEClient.normalize_series("st") == "ST"

    def test_is_traded_series(self):
        assert NSEClient.is_traded_series("EQ") is True
        assert NSEClient.is_traded_series("IT") is True
        assert NSEClient.is_traded_series("XX") is False

    def test_normalize_company_name(self):
        assert NSEClient.normalize_company_name("  Reliance   Industries  ") == "RELIANCE INDUSTRIES"
        assert NSEClient.normalize_company_name("") == ""

    def test_parse_meta_status_listed(self):
        assert NSEClient.parse_meta_status({"isDelisted": "false", "isSuspended": "false"}) == "LISTED"

    def test_parse_meta_status_delisted(self):
        assert NSEClient.parse_meta_status({"isDelisted": "true", "isSuspended": "false"}) == "DELISTED"

    def test_parse_meta_status_suspended(self):
        assert NSEClient.parse_meta_status({"isDelisted": "false", "isSuspended": "true"}) == "SUSPENDED"

    def test_parse_meta_status_error(self):
        assert NSEClient.parse_meta_status({"error": "timeout"}) == "UNKNOWN"

    def test_infer_sector_industry(self):
        meta = {"sector": "Technology", "industry": "Software"}
        sector, industry = NSEClient.infer_sector_industry(meta)
        assert sector == "Technology"
        assert industry == "Software"

    def test_infer_sector_industry_empty(self):
        meta = {"sector": "", "industry": ""}
        sector, industry = NSEClient.infer_sector_industry(meta)
        assert sector is None
        assert industry is None


class TestUniverseSyncService:
    @pytest.fixture
    def mock_nse(self):
        client = MagicMock(spec=NSEClient)
        return client

    @pytest.fixture
    def service(self, mock_nse):
        return UniverseSyncService(nse_client=mock_nse)

    @patch("app.services.universe_sync_service.supabase")
    def test_new_security_inserted(self, mock_supabase, service, mock_nse):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"symbol": "RELIANCE", "isin": "INE002A01018"}
        ]

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "RELIANCE", "Series": "EQ", "Security Name": "Reliance Industries Limited"}
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "RELIANCE",
            "companyName": "Reliance Industries Limited",
            "isin": "INE002A01018",
            "isDelisted": "false",
            "isSuspended": "false",
            "sector": "Energy",
            "industry": "Oil & Gas",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        report = service.run()
        assert report.new_securities == 1
        assert report.newly_listed == 1
        assert report.final_status == "SUCCESS"
        mock_supabase.table.return_value.insert.assert_called_once()

    @patch("app.services.universe_sync_service.supabase")
    def test_idempotent_second_run(self, mock_supabase, service, mock_nse):
        existing_data = [
            {
                "id": "uuid-1",
                "symbol": "RELIANCE",
                "company_name": "RELIANCE INDUSTRIES LIMITED",
                "isin": "INE002A01018",
                "exchange": "NSE",
                "status": "LISTED",
                "last_synced_at": None,
                "is_active": True,
            }
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = existing_data
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = existing_data

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "RELIANCE", "Series": "EQ", "Security Name": "Reliance Industries Limited"}
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "RELIANCE",
            "companyName": "Reliance Industries Limited",
            "isin": "INE002A01018",
            "isDelisted": "false",
            "isSuspended": "false",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        report = service.run()
        assert report.unchanged_securities == 1
        assert report.new_securities == 0
        assert report.final_status == "SUCCESS"
        mock_supabase.table.return_value.insert.assert_not_called()

    @patch("app.services.universe_sync_service.supabase")
    def test_company_name_change_updates(self, mock_supabase, service, mock_nse):
        existing_data = [
            {
                "id": "uuid-1",
                "symbol": "RELIANCE",
                "company_name": "Old Name Limited",
                "isin": "INE002A01018",
                "exchange": "NSE",
                "status": "LISTED",
                "last_synced_at": None,
            }
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = existing_data
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = existing_data

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "RELIANCE", "Series": "EQ", "Security Name": "New Name Limited"}
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "RELIANCE",
            "companyName": "New Name Limited",
            "isin": "INE002A01018",
            "isDelisted": "false",
            "isSuspended": "false",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        report = service.run()
        assert report.updated_securities == 1
        assert report.unchanged_securities == 0
        update_call = mock_supabase.table.return_value.update.call_args
        assert "company_name" in update_call[0][0]

    @patch("app.services.universe_sync_service.supabase")
    def test_symbol_change_same_isin(self, mock_supabase, service, mock_nse):
        existing_data = [
            {
                "id": "uuid-1",
                "symbol": "OLDSYMBOL",
                "company_name": "Reliance Industries Limited",
                "isin": "INE002A01018",
                "exchange": "NSE",
                "status": "LISTED",
                "last_synced_at": None,
            }
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = existing_data
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = existing_data

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "NEWSYMBOL", "Series": "EQ", "Security Name": "Reliance Industries Limited"}
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "NEWSYMBOL",
            "companyName": "Reliance Industries Limited",
            "isin": "INE002A01018",
            "isDelisted": "false",
            "isSuspended": "false",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        report = service.run()
        assert report.symbol_changes == 1
        assert report.updated_securities == 1
        update_call = mock_supabase.table.return_value.update.call_args
        assert update_call[0][0]["symbol"] == "NEWSYMBOL"

    @patch("app.services.universe_sync_service.supabase")
    def test_nse_failure_preserves_records(self, mock_supabase, service, mock_nse):
        existing_data = [
            {
                "id": "uuid-1",
                "symbol": "RELIANCE",
                "company_name": "Reliance Industries Limited",
                "isin": "INE002A01018",
                "exchange": "NSE",
                "status": "LISTED",
                "last_synced_at": None,
            }
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = existing_data

        mock_nse.fetch_sec_list.side_effect = Exception("NSE download failed")

        report = service.run()
        assert report.final_status == "FAILED"
        assert report.updated_securities == 0
        assert report.new_securities == 0
        mock_supabase.table.return_value.delete.assert_not_called()

    @patch("app.services.universe_sync_service.supabase")
    def test_malformed_rows_skipped(self, mock_supabase, service, mock_nse):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "", "Series": "EQ", "Security Name": "Bad Row"},
            {"Symbol": "RELIANCE", "Series": "EQ", "Security Name": "Reliance Industries Limited"},
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "RELIANCE",
            "companyName": "Reliance Industries Limited",
            "isin": "INE002A01018",
            "isDelisted": "false",
            "isSuspended": "false",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        report = service.run()
        assert report.failed_rows == 1
        assert report.new_securities == 1
        mock_supabase.table.return_value.insert.assert_called_once()

    @patch("app.services.universe_sync_service.supabase")
    def test_dry_run_no_writes(self, mock_supabase, mock_nse):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "RELIANCE", "Series": "EQ", "Security Name": "Reliance Industries Limited"}
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "RELIANCE",
            "companyName": "Reliance Industries Limited",
            "isin": "INE002A01018",
            "isDelisted": "false",
            "isSuspended": "false",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        service = UniverseSyncService(nse_client=mock_nse, dry_run=True)
        report = service.run()
        assert report.new_securities == 1
        assert report.newly_listed == 1
        assert report.final_status == "SUCCESS"
        mock_supabase.table.return_value.insert.assert_not_called()
        mock_supabase.table.return_value.update.assert_not_called()

    @patch("app.services.universe_sync_service.supabase")
    def test_new_security_gets_newly_listed_status(self, mock_supabase, service, mock_nse):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"}
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "NEWCO",
            "companyName": "New Company Limited",
            "isin": "INENEW001",
            "isDelisted": "false",
            "isSuspended": "false",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        report = service.run()
        assert report.new_securities == 1
        assert report.newly_listed == 1
        insert_call = mock_supabase.table.return_value.insert.call_args
        assert insert_call[0][0]["status"] == "NEWLY_LISTED"

    @patch("app.services.universe_sync_service.supabase")
    def test_nse_only_scope_preserves_other_exchanges(self, mock_supabase, mock_nse):
        existing_data = [
            {
                "id": "nse-uuid",
                "symbol": "RELIANCE",
                "company_name": "RELIANCE INDUSTRIES LIMITED",
                "isin": "INE002A01018",
                "exchange": "NSE",
                "status": "LISTED",
                "last_synced_at": None,
                "is_active": True,
            },
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = existing_data
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = existing_data

        mock_nse.fetch_sec_list.return_value = [
            {"Symbol": "RELIANCE", "Series": "EQ", "Security Name": "Reliance Industries Limited"}
        ]
        mock_nse.equity_meta_info.return_value = {
            "symbol": "RELIANCE",
            "companyName": "Reliance Industries Limited",
            "isin": "INE002A01018",
            "isDelisted": "false",
            "isSuspended": "false",
        }
        mock_nse.fetch_active_securities_isin.return_value = {}

        service = UniverseSyncService(nse_client=mock_nse, dry_run=True)
        report = service.run()
        assert report.unchanged_securities == 1
        assert report.new_securities == 0
        assert report.final_status == "SUCCESS"
        mock_supabase.table.return_value.update.assert_not_called()
        mock_supabase.table.return_value.delete.assert_not_called()


class TestPersonalSyncService:
    @pytest.fixture
    def mock_personal_supabase(self):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        client.table.return_value.upsert.return_value.execute.return_value.data = [
            {"symbol": "RELIANCE", "exchange": "NSE"}
        ]
        return client

    @patch("app.services.personal_sync_service.main_supabase")
    def test_new_stock_inserted_in_personal(
        self, mock_main_supabase, mock_personal_supabase, monkeypatch
    ):
        monkeypatch.setenv("PERSONAL_SUPABASE_URL", "http://test-personal")
        monkeypatch.setenv("PERSONAL_SUPABASE_SERVICE_ROLE_KEY", "test-key")

        mock_main_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {
                "symbol": "RELIANCE",
                "company_name": "Reliance Industries Limited",
                "isin": "INE002A01018",
                "exchange": "NSE",
                "sector": "Energy",
                "industry": "Oil & Gas",
                "status": "LISTED",
                "is_active": True,
                "first_listed_date": None,
            }
        ]
        mock_personal_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        mock_personal_supabase.table.return_value.upsert.return_value.execute.return_value.data = [
            {"symbol": "RELIANCE", "exchange": "NSE"}
        ]

        service = PersonalSyncService.__new__(PersonalSyncService)
        service.personal_supabase = mock_personal_supabase

        report = service.run()
        assert report.inserted == 1
        assert report.updated == 0
        assert report.final_status == "SUCCESS"

    @patch("app.services.personal_sync_service.main_supabase")
    def test_idempotent_personal_sync(
        self, mock_main_supabase, mock_personal_supabase, monkeypatch
    ):
        monkeypatch.setenv("PERSONAL_SUPABASE_URL", "http://test-personal")
        monkeypatch.setenv("PERSONAL_SUPABASE_SERVICE_ROLE_KEY", "test-key")

        mock_main_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {
                "symbol": "RELIANCE",
                "company_name": "Reliance Industries Limited",
                "isin": "INE002A01018",
                "exchange": "NSE",
                "sector": "Energy",
                "industry": "Oil & Gas",
                "status": "LISTED",
                "is_active": True,
                "first_listed_date": None,
            }
        ]
        mock_personal_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": "personal-uuid",
                "symbol": "RELIANCE",
                "company_name": "Reliance Industries Limited",
                "exchange": "NSE",
                "source": "main_pofit",
                "pofit_scores": [],
                "decisions": [],
                "paper_trades": [],
            }
        ]
        mock_personal_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": "personal-uuid"}
        ]

        service = PersonalSyncService.__new__(PersonalSyncService)
        service.personal_supabase = mock_personal_supabase

        report = service.run()
        assert report.updated == 1
        assert report.inserted == 0
        assert report.final_status == "SUCCESS"
