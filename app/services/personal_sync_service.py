import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from app.core.supabase import supabase as main_supabase

logger = logging.getLogger(__name__)

PERSONAL_SUPABASE_URL = os.getenv("PERSONAL_SUPABASE_URL")
PERSONAL_SUPABASE_SERVICE_ROLE_KEY = os.getenv("PERSONAL_SUPABASE_SERVICE_ROLE_KEY")


@dataclass
class PersonalSyncReport:
    total_main_stocks: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    sync_duration_seconds: float = 0.0
    final_status: str = "pending"
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Personal sync: main={self.total_main_stocks} "
            f"inserted={self.inserted} updated={self.updated} skipped={self.skipped} "
            f"failed={self.failed} duration={self.sync_duration_seconds:.1f}s "
            f"status={self.final_status}"
        )


class PersonalSyncService:
    def __init__(self) -> None:
        if not PERSONAL_SUPABASE_URL or not PERSONAL_SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError(
                "PERSONAL_SUPABASE_URL and PERSONAL_SUPABASE_SERVICE_ROLE_KEY must be set"
            )
        self.personal_supabase: Client = create_client(
            PERSONAL_SUPABASE_URL, PERSONAL_SUPABASE_SERVICE_ROLE_KEY
        )

    def _fetch_main_universe(self) -> List[Dict[str, Any]]:
        result = (
            main_supabase.table("stocks")
            .select("symbol, company_name, isin, exchange, sector, industry, status, first_listed_date")
            .eq("exchange", "NSE")
            .eq("is_active", True)
            .execute()
        )
        return result.data or []

    def _fetch_personal_universe(self) -> Dict[str, Dict[str, Any]]:
        result = (
            self.personal_supabase.table("stocks")
            .select("id, symbol, company_name, exchange, source")
            .eq("exchange", "NSE")
            .execute()
        )
        records = result.data or []
        by_key: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            key = f"{rec['symbol']}|{rec['exchange']}"
            by_key[key] = rec
        return by_key

    @staticmethod
    def _build_personal_payload(main_stock: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": main_stock["symbol"],
            "company_name": main_stock["company_name"],
            "exchange": main_stock.get("exchange", "NSE"),
            "country": "IN",
            "isin": main_stock.get("isin"),
            "sector": main_stock.get("sector"),
            "industry": main_stock.get("industry"),
            "is_active": main_stock.get("is_active", True),
            "status": main_stock.get("status", "ACTIVE"),
            "first_listed_date": main_stock.get("first_listed_date"),
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "source": "main_pofit",
        }

    def run(self) -> PersonalSyncReport:
        start = time.time()
        report = PersonalSyncReport()
        logger.info("Starting Main Pofit -> Pofit Personal sync")

        try:
            main_stocks = self._fetch_main_universe()
        except Exception as exc:
            logger.error("Failed to fetch Main Pofit universe: %s", exc)
            report.final_status = "FAILED"
            report.errors.append(str(exc))
            report.sync_duration_seconds = time.time() - start
            return report

        report.total_main_stocks = len(main_stocks)

        try:
            personal_stocks = self._fetch_personal_universe()
        except Exception as exc:
            logger.error("Failed to fetch Pofit Personal universe: %s", exc)
            report.final_status = "FAILED"
            report.errors.append(str(exc))
            report.sync_duration_seconds = time.time() - start
            return report

        for main_stock in main_stocks:
            symbol = main_stock.get("symbol", "").upper()
            exchange = main_stock.get("exchange", "NSE").upper()
            key = f"{symbol}|{exchange}"
            payload = self._build_personal_payload(main_stock)

            existing = personal_stocks.get(key)
            if existing is None:
                try:
                    self.personal_supabase.table("stocks").upsert(
                        payload, on_conflict="symbol,exchange"
                    ).execute()
                    report.inserted += 1
                except Exception as exc:
                    logger.error("Failed to insert %s into personal: %s", symbol, exc)
                    report.failed += 1
                    report.errors.append(f"insert {symbol}: {exc}")
            else:
                update_fields = {
                    "company_name": payload["company_name"],
                    "isin": payload["isin"],
                    "sector": payload["sector"],
                    "industry": payload["industry"],
                    "is_active": payload["is_active"],
                    "status": payload["status"],
                    "first_listed_date": payload["first_listed_date"],
                    "last_synced_at": payload["last_synced_at"],
                    "source": "main_pofit",
                }
                try:
                    self.personal_supabase.table("stocks").update(update_fields).eq(
                        "id", existing["id"]
                    ).execute()
                    report.updated += 1
                except Exception as exc:
                    logger.error("Failed to update %s in personal: %s", symbol, exc)
                    report.failed += 1
                    report.errors.append(f"update {symbol}: {exc}")

        report.sync_duration_seconds = time.time() - start
        if report.failed > 0 and (report.inserted + report.updated) == 0:
            report.final_status = "FAILED"
        elif report.failed > 0:
            report.final_status = "PARTIAL_SUCCESS"
        else:
            report.final_status = "SUCCESS"

        logger.info(report.summary())
        return report
