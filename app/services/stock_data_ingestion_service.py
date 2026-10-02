import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase import supabase
from app.repositories.stock_repository import StockRepository
from app.repositories.stock_data_repository import StockDataRepository
from app.services.market_data_service import MarketDataService, CACHE_TTL
from app.services.yahoo_service import MarketDataUnavailableError, SymbolNotFoundError
from app.utils.postgrest import batched, normalized_unique_symbols, SYMBOL_BATCH_SIZE

logger = logging.getLogger(__name__)

# Each full Yahoo refresh costs roughly 258 KB. The job runs every 4 hours
# (6 times a day), so the default batch size of 30 already accounted for
# about 45 MB/day on its own, on top of the Top Picks jobs. 12 keeps the
# scheduled ingestion inside the shared daily egress budget while still
# rotating the freshest symbols each cycle; a symbol that has never been
# ingested is always prioritised over one that merely went stale.
INGESTION_BATCH_SIZE = int(os.getenv("STOCK_DATA_INGESTION_BATCH_SIZE", "12"))
CACHE_CHECK_BATCH_SIZE = SYMBOL_BATCH_SIZE


@dataclass
class IngestionReport:
    total_universe: int = 0
    missing_cache: int = 0
    stale_cache: int = 0
    fresh_cache: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    yahoo_rate_limited: bool = False
    duration_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"StockData ingestion: universe={self.total_universe} "
            f"missing={self.missing_cache} stale={self.stale_cache} fresh={self.fresh_cache} "
            f"processed={self.processed} succeeded={self.succeeded} failed={self.failed} "
            f"skipped={self.skipped} rate_limited={self.yahoo_rate_limited} "
            f"duration={self.duration_seconds:.1f}s"
        )


class StockDataIngestionService:
    """
    Scheduled service to refresh stock_data cache for NSE universe.

    Design:
    - Fetches NSE universe from stocks table (paginated)
    - Batch-checks stock_data.updated_at to find missing/stale entries (no N+1)
    - Processes only symbols needing refresh, respecting CACHE_TTL
    - Uses MarketDataService.refresh_stock() to reuse existing Yahoo logic
    - Bounded batch size with sequential Yahoo requests
    - Backs off on Yahoo 429 / rate limit errors
    - One symbol failure does not terminate the batch
    """

    def __init__(
        self,
        batch_size: Optional[int] = None,
        market_service: Optional[MarketDataService] = None,
    ):
        self.batch_size = batch_size or INGESTION_BATCH_SIZE
        self.market = market_service or MarketDataService()
        self.stock_repo = StockRepository()
        self.stock_data_repo = StockDataRepository()

    def _get_nse_universe(self) -> List[Dict[str, Any]]:
        """Get all active NSE stocks from stocks table."""
        return self.stock_repo.list_all_by_country("IN")

    def _check_cache_status_batched(
        self, symbols: List[str]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Batch check stock_data cache for multiple symbols.

        Returns dict mapping symbol -> cached row (with updated_at) or None if missing.
        Only selects required columns to minimize egress.
        """
        if not symbols:
            return {}

        symbols_upper = normalized_unique_symbols(symbols)
        cache_map: Dict[str, Optional[Dict[str, Any]]] = {}

        for batch in batched(symbols_upper, CACHE_CHECK_BATCH_SIZE):
            or_filter = ",".join(f"symbol.eq.{s}" for s in batch)
            try:
                result = (
                    supabase.table("stock_data")
                    .select("symbol, updated_at, cache_status")
                    .or_(or_filter)
                    .execute()
                )
                for row in result.data or []:
                    cache_map[row["symbol"]] = row
            except Exception as exc:
                logger.warning("Failed to fetch cache status for batch: %s", exc)

        for sym in symbols_upper:
            if sym not in cache_map:
                cache_map[sym] = None

        return cache_map

    def _classify_symbols(
        self, universe: List[Dict[str, Any]], cache_map: Dict[str, Optional[Dict[str, Any]]]
    ) -> tuple[List[str], List[tuple[str, datetime]], List[str]]:
        """
        Classify symbols into missing, stale, fresh based on cache status.

        Returns: (missing_symbols, stale_symbols_with_updated, fresh_symbols)
        stale_symbols_with_updated is a list of (symbol, updated_at) tuples
        """
        missing: List[str] = []
        stale: List[tuple[str, datetime]] = []
        fresh: List[str] = []

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

        for stock in universe:
            symbol = stock["symbol"].upper()
            cached = cache_map.get(symbol)

            if cached is None:
                missing.append(symbol)
                continue

            updated_str = cached.get("updated_at")
            cache_status = cached.get("cache_status")

            if not updated_str or cache_status not in {"fresh", "partial"}:
                missing.append(symbol)
                continue

            try:
                updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                if updated.tzinfo is not None:
                    updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                missing.append(symbol)
                continue

            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            if now_naive - updated >= CACHE_TTL:
                stale.append((symbol, updated))
            else:
                fresh.append(symbol)

        return missing, stale, fresh

    def _refresh_symbol(self, symbol: str) -> bool:
        """
        Refresh a single symbol using MarketDataService.

        Returns True on success, False on failure (logs error).
        """
        try:
            self.market.refresh_stock(symbol)
            return True
        except SymbolNotFoundError as exc:
            logger.warning("Symbol not found during ingestion: %s", exc)
            return False
        except MarketDataUnavailableError as exc:
            msg = str(exc).lower()
            if any(marker in msg for marker in ("429", "rate limit", "too many requests")):
                logger.warning("Yahoo rate limit hit for %s: %s", symbol, exc)
                raise  # Re-raise to trigger batch backoff
            logger.warning("Market data unavailable for %s: %s", symbol, exc)
            return False
        except Exception as exc:
            logger.exception("Unexpected error refreshing %s: %s", symbol, exc)
            return False

    def run(self) -> IngestionReport:
        start = time.time()
        report = IngestionReport()

        logger.info("Starting stock_data ingestion for NSE universe")

        try:
            universe = self._get_nse_universe()
        except Exception as exc:
            logger.error("Failed to fetch NSE universe: %s", exc)
            report.errors.append(str(exc))
            report.duration_seconds = time.time() - start
            return report

        report.total_universe = len(universe)
        if not universe:
            logger.info("No NSE stocks found")
            report.duration_seconds = time.time() - start
            return report

        symbols = [s["symbol"] for s in universe]
        cache_map = self._check_cache_status_batched(symbols)

        missing, stale, fresh = self._classify_symbols(universe, cache_map)

        report.missing_cache = len(missing)
        report.stale_cache = len(stale)
        report.fresh_cache = len(fresh)

        # Sort stale by oldest updated_at first (ascending)
        stale_sorted = sorted(stale, key=lambda x: x[1])
        stale_symbols = [symbol for symbol, _ in stale_sorted]

        # Priority: missing first, then oldest stale
        to_process = missing + stale_symbols
        logger.info(
            "Cache status: missing=%d stale=%d fresh=%d -> processing %d symbols",
            len(missing), len(stale), len(fresh), len(to_process)
        )

        if not to_process:
            report.duration_seconds = time.time() - start
            return report

        for i, symbol in enumerate(to_process):
            if i >= self.batch_size:
                logger.info("Batch size limit reached (%d), stopping", self.batch_size)
                report.skipped = len(to_process) - i
                break

            if report.yahoo_rate_limited:
                logger.info("Yahoo rate limited, stopping batch early")
                report.skipped = len(to_process) - i
                break

            report.processed += 1

            try:
                success = self._refresh_symbol(symbol)
                if success:
                    report.succeeded += 1
                else:
                    report.failed += 1
                    report.errors.append(f"{symbol}: refresh failed")
            except MarketDataUnavailableError as exc:
                msg = str(exc).lower()
                if any(marker in msg for marker in ("429", "rate limit", "too many requests")):
                    report.yahoo_rate_limited = True
                    report.failed += 1
                    report.errors.append(f"{symbol}: Yahoo rate limited")
                    logger.warning("Yahoo rate limit detected, will back off")
                    time.sleep(2)  # Brief backoff before stopping
                    continue
                report.failed += 1
                report.errors.append(f"{symbol}: {exc}")

        report.duration_seconds = time.time() - start
        logger.info(report.summary())
        if report.errors:
            logger.warning("Ingestion errors (first 10): %s", report.errors[:10])

        return report