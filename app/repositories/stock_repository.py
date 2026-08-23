from typing import List, Optional, Dict, Any

from app.core.supabase import supabase


class StockRepository:
    """Repository for accessing stock metadata stored in Supabase."""

    def search(
        self,
        query: str,
        country: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:

        request = (
            supabase.table("stocks")
            .select(
                "symbol, company_name, exchange, country, sector, industry"
            )
            .eq("is_active", True)
        )

        if country:
            request = request.eq("country", country)

        request = (
            request.or_(
                f"symbol.ilike.%{query}%,company_name.ilike.%{query}%"
            )
            .limit(limit)
        )

        result = request.execute()

        data = result.data or []

        for stock in data:
            if not stock.get("sector"):
                stock["sector"] = stock.get("industry")

        return data

    def get_by_symbol(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:

        result = (
            supabase.table("stocks")
            .select("*")
            .eq("symbol", symbol.upper())
            .eq("is_active", True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    def list_all_active(self) -> List[Dict[str, Any]]:
        """Return the full active stock universe for benchmarking."""
        result = (
            supabase.table("stocks")
            .select("symbol,sector,industry")
            .eq("is_active", True)
            .execute()
        )

        return result.data or []

    def list_country(
        self,
        country: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:

        result = (
            supabase.table("stocks")
            .select(
                "symbol, company_name, exchange, country"
            )
            .eq("country", country)
            .eq("is_active", True)
            .limit(limit)
            .execute()
        )

        return result.data or []

    def list_all_by_country(
        self,
        country: str,
    ) -> List[Dict[str, Any]]:
        """
        Returns every active stock for the specified country.
        Used by the Daily Top Picks engine.
        """

        all_stocks: List[Dict[str, Any]] = []
        page_size = 1000
        offset = 0

        while True:
            result = (
                supabase.table("stocks")
                .select(
                    "symbol, company_name, exchange, country"
                )
                .eq("country", country)
                .eq("is_active", True)
                .range(offset, offset + page_size - 1)
                .execute()
            )

            rows = result.data or []

            if not rows:
                break

            all_stocks.extend(rows)

            if len(rows) < page_size:
                break

            offset += page_size

        return all_stocks

    def list_by_status(
        self,
        status: str,
        country: Optional[str] = None,
        exchange: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Returns stocks by radar status.
        Used by the Radar endpoint.
        """
        query = (
            supabase.table("stocks")
            .select(
                "symbol, company_name, exchange, country, isin, status, first_listed_date, last_synced_at"
            )
            .eq("status", status)
            .limit(limit)
        )

        if country:
            query = query.eq("country", country)

        if exchange:
            query = query.eq("exchange", exchange)

        result = query.execute()

        return result.data or []

    def update_metadata(
        self,
        symbol: str,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> None:

        payload = {}

        final_sector = sector or industry

        if final_sector:
            payload["sector"] = final_sector

        if industry:
            payload["industry"] = industry

        if not payload:
            return

        result = (
            supabase.table("stocks")
            .update(payload)
            .eq("symbol", symbol.upper())
            .execute()
        )

        if not result.data:
            (
                supabase.table("stocks")
                .update(payload)
                .eq("symbol", symbol.upper() + ".NS")
                .execute()
            )