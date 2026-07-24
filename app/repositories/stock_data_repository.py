from typing import Any, Dict, Optional

from app.core.supabase import supabase


class StockDataRepository:
    """Repository for cached financial data stored in Supabase."""

    def get(self, symbol: str) -> Optional[Dict[str, Any]]:
        result = (
            supabase.table("stock_data")
            .select("*")
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    def save(self, symbol: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = {
            "symbol": symbol.upper(),
            **payload,
        }

        result = (
            supabase.table("stock_data")
            .upsert(data)
            .execute()
        )

        return result.data[0]

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