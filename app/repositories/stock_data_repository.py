from typing import Any, Dict, Optional, List

from app.core.supabase import supabase
from app.utils.postgrest import (
    SYMBOL_BATCH_SIZE,
    batched,
    normalized_unique_symbols,
)


class StockDataRepository:
    """Repository for cached financial data stored in Supabase."""

    def get(self, symbol: str) -> Optional[Dict[str, Any]]:
        result = (
            supabase.table("stock_data")
            .select("symbol, quote_json, metrics_json, score_json, updated_at")
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    def get_many(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Return cached stock_data for multiple symbols.

        Uses an OR filter on `symbol` and returns only the columns
        needed for score sync and data-quality checks.
        """
        if not symbols:
            return []

        symbols_upper = [s.upper() for s in symbols]
        or_filter = ",".join(
            "symbol.eq.{}".format(s) for s in symbols_upper
        )

        result = (
            supabase.table("stock_data")
            .select("symbol, quote_json, metrics_json, score_json, updated_at")
            .or_(or_filter)
            .execute()
        )

        return result.data or []

    def get_many_minimal(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Return minimal cached stock_data for multiple symbols.

        Selects only: symbol, score_json, updated_at
        Used by ScoreSyncService which only needs score data.
        """
        if not symbols:
            return []

        symbols_upper = [s.upper() for s in symbols]
        or_filter = ",".join(
            "symbol.eq.{}".format(s) for s in symbols_upper
        )

        result = (
            supabase.table("stock_data")
            .select("symbol, score_json, updated_at")
            .or_(or_filter)
            .execute()
        )

        return result.data or []

    def save(self, symbol: str, payload: Dict[str, Any]) -> None:
        data = {
            "symbol": symbol.upper(),
            **payload,
        }

        supabase.table("stock_data").upsert(data).execute()

    def list_all_metrics(self) -> List[Dict[str, Any]]:
        """Return cached metrics for the full stock universe."""
        result = (
            supabase.table("stock_data")
            .select("symbol,metrics_json")
            .execute()
        )

        return result.data or []

    def exists(self, symbol: str) -> bool:
        result = (
            supabase.table("stock_data")
            .select("symbol")
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        return len(result.data) > 0

    def get_last_updated(self, symbol: str):
        result = (
            supabase.table("stock_data")
            .select("updated_at")
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]["updated_at"]

    def get_many_for_top_picks(
        self,
        symbols: List[str],
    ) -> List[Dict[str, Any]]:
        """Return cached stock_data for multiple symbols for Top Picks.

        Selects only: symbol, eligibility_json, score_json, cache_status, updated_at

        Symbols are requested in batches of `SYMBOL_BATCH_SIZE`. A single
        request covering the whole universe builds an `or=(symbol.eq.X,...)`
        filter that travels in the request URL and is rejected by the gateway
        with HTTP 400, so the universe must not be sent in one request.
        """
        if not symbols:
            return []

        symbols_upper = normalized_unique_symbols(symbols)
        if not symbols_upper:
            return []

        results: List[Dict[str, Any]] = []

        for batch in batched(symbols_upper, SYMBOL_BATCH_SIZE):
            or_filter = ",".join(
                "symbol.eq.{}".format(s) for s in batch
            )

            result = (
                supabase.table("stock_data")
                .select("symbol, eligibility_json, score_json, cache_status, updated_at")
                .or_(or_filter)
                .execute()
            )

            results.extend(result.data or [])

        return results

    def get_quote_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return lightweight quote cache data for a symbol."""
        result = (
            supabase.table("stock_data")
            .select("symbol, quote_json, updated_at")
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    def get_score_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return lightweight score cache data for a symbol."""
        result = (
            supabase.table("stock_data")
            .select("symbol, quote_json, metrics_json, score_json, updated_at")
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    def get_for_top_picks(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return cached stock_data for a single symbol for Top Picks.

        Selects only: symbol, eligibility_json, score_json, cache_status, updated_at
        """
        result = (
            supabase.table("stock_data")
            .select("symbol, eligibility_json, score_json, cache_status, updated_at")
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]