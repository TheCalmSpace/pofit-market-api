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


# PostgREST caps a single response at 1000 rows (the project `max-rows`
# setting), so any read of the NSE universe must be paginated. The universe is
# larger than that today, so an unpaginated read silently returns only the
# first page and every stock outside it is misclassified as newly listed.
UNIVERSE_PAGE_SIZE = 1000


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
    insert_failures: int = 0
    meta_requests: int = 0
    sync_duration_seconds: float = 0.0
    final_status: str = "pending"
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Sync complete: discovered={self.nse_records_discovered} "
            f"new={self.new_securities} updated={self.updated_securities} "
            f"unchanged={self.unchanged_securities} symbol_changes={self.symbol_changes} "
            f"newly_listed={self.newly_listed} inactive={self.inactive} "
            f"failed={self.failed_rows} insert_failures={self.insert_failures} "
            f"nse_meta_requests={self.meta_requests} "
            f"duration={self.sync_duration_seconds:.1f}s "
            f"status={self.final_status}"
        )


class UniverseSyncService:
    def __init__(self, nse_client: Optional[NSEClient] = None, dry_run: bool = False) -> None:
        self.nse = nse_client or NSEClient()
        self.dry_run = dry_run
        self.report = SyncReport()

    def _fetch_existing_universe(self) -> Dict[str, Dict[str, Any]]:
        """Load the complete existing NSE universe.

        Paginated because PostgREST returns at most 1000 rows per response and
        the NSE universe is larger than that. Reading only the first page would
        hide existing stocks from `_find_existing`, which would make the sync
        attempt to re-insert them as newly listed.
        """
        records: List[Dict[str, Any]] = []
        offset = 0

        while True:
            result = (
                supabase.table("stocks")
                .select("id, symbol, company_name, isin, exchange, status, last_synced_at")
                .eq("exchange", "NSE")
                .range(offset, offset + UNIVERSE_PAGE_SIZE - 1)
                .execute()
            )
            rows = result.data or []

            if not rows:
                break

            records.extend(rows)

            if len(rows) < UNIVERSE_PAGE_SIZE:
                break

            offset += UNIVERSE_PAGE_SIZE

        logger.info("Loaded %d existing NSE securities from Supabase", len(records))

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
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        symbol = sec["Symbol"].upper()
        company_name = NSEClient.normalize_company_name(sec["Security Name"])
        isin = (meta.get("isin") or "").strip() or None
        if not isin:
            isin = isin_map.get(company_name)

        meta_unavailable = bool(meta.get("error") or meta.get("skipped"))

        if meta_unavailable:
            # The bulk active-securities file is authoritative for "this equity
            # is currently listed": it only lists live, active instruments. Use
            # it to resolve the status instead of assuming LISTED for everything
            # we could not verify individually, which previously pinned the
            # entire NSE universe at `UNKNOWN`.
            if isin:
                nse_status = "LISTED"
            else:
                nse_status = (existing or {}).get("status") or "LISTED"
            if existing:
                isin = existing.get("isin") or isin
                # is_active will be derived from preserved status below
        else:
            nse_status = NSEClient.parse_meta_status(meta)

        sector, industry = NSEClient.infer_sector_industry(meta)

        if meta_unavailable and existing:
            # Never overwrite a stored sector/industry with NULL just because
            # NSE refused the metadata request.
            sector = existing.get("sector") or sector
            industry = existing.get("industry") or industry

        # NOTE: the NSE `series` is intentionally not persisted. It is only
        # used to decide whether a row is a tradable series (see `run`), and
        # `public.stocks` has no `series` column. Sending it made every
        # new-listing INSERT fail with SQLSTATE 42703
        # ("column stocks.series does not exist"), so no new NSE listing was
        # ever stored.
        payload: Dict[str, Any] = {
            "symbol": symbol,
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
        elif nse_status in ("DELISTED", "SUSPENDED"):
            payload["is_active"] = False
        elif existing and "is_active" in existing:
            # Preserve is_active for other statuses (e.g., NEWLY_LISTED) when meta fails
            payload["is_active"] = existing.get("is_active")

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

        # `nseindia.com` is aggressively protected: it answers 403 to anything
        # that is not a warmed-up browser session. `equity_meta_info` was
        # therefore called once per symbol on every nightly run (3,574 requests
        # at 3 req/s, roughly 20 minutes of traffic) and returned an error for
        # effectively all of them, which is why 3,535 of 3,566 NSE rows carry
        # `status = 'UNKNOWN'` with a NULL isin, sector and industry.
        #
        # Per-symbol metadata is now requested only for symbols that are new to
        # the table, where it enriches the row with sector and industry and is
        # worth a single request. Everything already known is identified from
        # `sec_list.csv` plus the bulk active-securities CSV, both of which come
        # from `nsearchives.nseindia.com` and still respond normally, so the
        # nightly run costs two file downloads instead of 3,574 HTTP requests.
        seen_symbols: Set[str] = set()
        seen_isins: Set[str] = set()
        meta_calls = 0

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

            company_name = NSEClient.normalize_company_name(sec["Security Name"])
            bulk_isin = isin_map.get(company_name)

            # Metadata is requested only for symbols the bulk
            # active-securities file cannot vouch for. Those are the symbols
            # whose ISIN is still unknown, so the metadata call is what lets
            # the existing row be matched by ISIN (catching an NSE symbol
            # rename) and what detects a DELISTED or SUSPENDED status. A
            # symbol the bulk file does list is resolved without any
            # per-symbol request. In practice that is a few hundred requests a
            # night rather than the whole universe.
            if bulk_isin:
                meta = {"symbol": symbol, "skipped": True}
            else:
                meta_calls += 1
                try:
                    meta = self.nse.equity_meta_info(symbol)
                except Exception as exc:
                    logger.warning("Failed to fetch meta for %s: %s", symbol, exc)
                    self.report.failed_rows += 1
                    self.report.errors.append(f"{symbol}: {exc}")
                    meta = {"symbol": symbol, "error": str(exc)}

            lookup_isin = (meta.get("isin") or "").strip() or bulk_isin

            rec = self._find_existing(lookup_isin, symbol, existing)

            payload = self._build_sync_payload(sec, meta, isin_map, rec)
            isin = payload.get("isin")
            if isin:
                if isin in seen_isins:
                    continue
                seen_isins.add(isin)

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
                        logger.info("Inserted newly listed security %s (%s)", symbol, isin)
                    except Exception as exc:
                        # A failed INSERT means a genuinely new listing was not
                        # stored. The row is still counted so the run continues,
                        # but the error is logged in full and forces the report
                        # to FAILED so the scheduled job exits non-zero instead
                        # of reporting a green run that silently dropped stocks.
                        message = f"insert {symbol} (isin={isin}): {exc}"
                        logger.error(
                            "Failed to insert newly listed security %s: %s: %s",
                            symbol,
                            type(exc).__name__,
                            exc,
                        )
                        self.report.failed_rows += 1
                        self.report.insert_failures += 1
                        self.report.errors.append(message)
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
        self.report.meta_requests = meta_calls
        if self.report.insert_failures > 0:
            # Never report success/partial success when new listings were
            # dropped by the database. The scheduled workflow turns FAILED into
            # a non-zero exit code.
            self.report.final_status = "FAILED"
        elif self.report.failed_rows > 0 and self.report.new_securities == 0 and self.report.updated_securities == 0:
            self.report.final_status = "FAILED"
        elif self.report.failed_rows > 0:
            self.report.final_status = "PARTIAL_SUCCESS"
        else:
            self.report.final_status = "SUCCESS"

        logger.info(self.report.summary())
        if self.report.errors:
            logger.error(
                "Universe sync finished with %d error(s); first 10: %s",
                len(self.report.errors),
                self.report.errors[:10],
            )
        return self.report
