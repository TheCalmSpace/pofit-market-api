from typing import Any, Dict, List, Optional

from app.core.supabase import supabase


class AlphaPortfolioRepository:
    TABLE = "alpha_portfolio"

    def get_all(self, market: str) -> List[Dict[str, Any]]:
        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", market)
            .execute()
        )

        return result.data or []

    def replace_all(self, market: str, rows: List[Dict[str, Any]]) -> None:
        (
            supabase.table(self.TABLE)
            .delete()
            .eq("market", market)
            .execute()
        )

        if rows:
            supabase.table(self.TABLE).insert(rows).execute()

    def insert(self, stock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        result = supabase.table(self.TABLE).insert(stock).execute()

        if not result.data:
            return None

        return result.data[0]

    def delete(self, symbol: str, market: Optional[str] = None) -> None:
        req = supabase.table(self.TABLE).delete().eq("symbol", symbol.upper())

        if market:
            req = req.eq("market", market)

        req.execute()

    def update(self, stock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = stock.get("symbol")

        if not symbol:
            return None

        req = supabase.table(self.TABLE).update(stock).eq("symbol", symbol.upper())

        market = stock.get("market")
        if market:
            req = req.eq("market", market)

        result = req.execute()

        if not result.data:
            return None

        return result.data[0]
