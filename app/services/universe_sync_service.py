import csv
import io
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.supabase import supabase
from app.services.nse_client import NSEClient

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    nse_records_discovered: int = 0
    new_securities: int = 0
    updated_securities: int = 0
    unchanged_securities: int = 0
    symbol_changes: int = 0
    newly_listed: int = 0
    inactive: int = 0
    failed_rows: int = 0
    sync_duration_seconds: float = 0.0
    final_status: str = "pending"
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Sync complete: discovered={self.nse_records_discovered} "
            f"new={self.new_securities} updated={self.updated_securities} "
            f"unchanged={self.unchanged_securities} symbol_changes={self.symbol_changes} "
            f"newly_listed={self.newly_listed} inactive={self.inactive} "
            f"failed={self.failed_rows} duration={self.sync_duration_seconds:.1f}s "
            f"status={self.final_status}"
        )


class UniverseSyncService:
    def __init__(self, nse_client: Optional[NSEClient] = None, dry_run: bool = False) -> None:
        self.nse = nse_client or NSEClient()
        self.dry_run = dry_run
        self.report = SyncReport()

    def _fetch_existing_universe(self) -> Dict[str, Dict[str, Any]]:
        result = (
            supabase.table("stocks")
            .select("id, symbol, company_name, isin, exchange, status, last_synced_at")
            .eq("exchange", "NSE")
            .execute()
        )
        records = result.data or []
        by_isin: Dict[str, Dict[str, Any]] = {}
        by_symbol: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            isin = rec.get("isin")
            symbol = rec.get("symbol", "").upper()
            if isin:
                by_isin[isin] = rec
            by_symbol[symbol] = rec
        return {"by_isin": by_isin, "by_symbol": by_symbol, "all": records}

    def _find_existing(
        self,
        isin: Optional[str],
        symbol: str,
        existing: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if isin and isin in existing["by_isin"]:
            return existing["by_isin"][isin]
        if symbol in existing["by_symbol"]:
            return existing["by_symbol"][symbol]
        return None

    def _build_sync_payload(
        self,
        sec: Dict[str, str],
        meta: Dict[str, Any],
        isin_map: Dict[str, str],
    ) -> Dict[str, Any]:
        symbol = sec["Symbol"].upper()
        series = NSEClient.normalize_series(sec.get("Series", "EQ"))
        company_name = NSEClient.normalize_company_name(sec["Security Name"])
        isin = (meta.get("isin") or "").strip() or None
        if not isin:
            isin = isin_map.get(company_name)

        nse_status = NSEClient.parse_meta_status(meta)
        sector, industry = NSEClient.infer_sector_industry(meta)

        payload: Dict[str, Any] = {
            "symbol": symbol,
            "series": series,
            "company_name": company_name,
            "isin": isin,
            "status": nse_status,
            "exchange": "NSE",
            "country": "IN",
            "sector": sector,
            "industry": industry,
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        }

        if nse_status == "LISTED":
            payload["is_active"] = True
        elif nse_status in ("DELISTED",):
            payload["is_active"] = False

        return payload

    def _detect_changes(
        self, existing: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        changes = {}
        fields_to_check = ["symbol", "company_name", "isin", "status", "sector", "industry", "is_active"]
        for field in fields_to_check:
            old_val = existing.get(field)
            new_val = payload.get(field)
            if old_val != new_val:
                changes[field] = new_val
        return changes

    def run(self) -> SyncReport:
        start = time.time()
        self.report = SyncReport()
        logger.info("Starting NSE universe sync")

        try:
            sec_list = self.nse.fetch_sec_list()
        except Exception as exc:
            logger.error("Failed to download sec_list.csv: %s", exc)
            self.report.final_status = "FAILED"
            self.report.errors.append(str(exc))
            self.report.sync_duration_seconds = time.time() - start
            return self.report

        self.report.nse_records_discovered = len(sec_list)
        existing = self._fetch_existing_universe()
        isin_map = self.nse.fetch_active_securities_isin()

        seen_symbols: Set[str] = set()
        seen_isins: Set[str] = set()

        for sec in sec_list:
            symbol = sec.get("Symbol", "").upper().strip()
            series = NSEClient.normalize_series(sec.get("Series", "EQ"))
            if not NSEClient.is_traded_series(series):
                continue
            if not symbol:
                self.report.failed_rows += 1
                continue

            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)

            try:
                meta = self.nse.equity_meta_info(symbol)
            except Exception as exc:
                logger.warning("Failed to fetch meta for %s: %s", symbol, exc)
                self.report.failed_rows += 1
                self.report.errors.append(f"{symbol}: {exc}")
                continue

            payload = self._build_sync_payload(sec, meta, isin_map)
            isin = payload.get("isin")
            if isin:
                if isin in seen_isins:
                    continue
                seen_isins.add(isin)

            rec = self._find_existing(isin, symbol, existing)
            if rec is None:
                payload["status"] = "NEWLY_LISTED"
                if self.dry_run:
                    self.report.new_securities += 1
                    self.report.newly_listed += 1
                    logger.info("[DRY RUN] Would insert %s with status NEWLY_LISTED", symbol)
                else:
                    try:
                        supabase.table("stocks").insert(payload).execute()
                        self.report.new_securities += 1
                        self.report.newly_listed += 1
                    except Exception as exc:
                        logger.error("Failed to insert %s: %s", symbol, exc)
                        self.report.failed_rows += 1
                        self.report.errors.append(f"insert {symbol}: {exc}")
            else:
                changes = self._detect_changes(rec, payload)
                if changes:
                    if self.dry_run:
                        self.report.updated_securities += 1
                        if changes.get("status") in ("DELISTED", "SUSPENDED"):
                            self.report.inactive += 1
                        if "symbol" in changes:
                            self.report.symbol_changes += 1
                        logger.info(
                            "[DRY RUN] Would update %s (id=%s) with changes: %s",
                            symbol,
                            rec.get("id"),
                            changes,
                        )
                    else:
                        try:
                            supabase.table("stocks").update(changes).eq("id", rec["id"]).execute()
                            self.report.updated_securities += 1
                            if changes.get("status") in ("DELISTED", "SUSPENDED"):
                                self.report.inactive += 1
                            if "symbol" in changes:
                                self.report.symbol_changes += 1
                        except Exception as exc:
                            logger.error("Failed to update %s: %s", symbol, exc)
                            self.report.failed_rows += 1
                            self.report.errors.append(f"update {symbol}: {exc}")
                else:
                    if not self.dry_run:
                        try:
                            supabase.table("stocks").update(
                                {"last_synced_at": payload["last_synced_at"]}
                            ).eq("id", rec["id"]).execute()
                        except Exception as exc:
                            logger.warning("Failed to update last_synced_at for %s: %s", symbol, exc)
                    self.report.unchanged_securities += 1

        self.report.sync_duration_seconds = time.time() - start
        if self.report.failed_rows > 0 and self.report.new_securities == 0 and self.report.updated_securities == 0:
            self.report.final_status = "FAILED"
        elif self.report.failed_rows > 0:
            self.report.final_status = "PARTIAL_SUCCESS"
        else:
            self.report.final_status = "SUCCESS"

        logger.info(self.report.summary())
        return self.report
