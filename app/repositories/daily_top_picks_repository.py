from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from app.core.supabase import supabase


class DailyTopPicksRepository:
    TABLE = "daily_top_picks"

    def replace_country(
        self,
        country: str,
        rows: List[Dict[str, Any]],
    ) -> None:

        (
            supabase.table(self.TABLE)
            .delete()
            .eq("country", country)
            .execute()
        )

        if rows:
            # Add generated_at timestamp to each row
            timestamp = datetime.now(timezone.utc).isoformat()
            rows_with_timestamp = [
                {**row, "generated_at": timestamp}
                for row in rows
            ]
            (
                supabase.table(self.TABLE)
                .insert(rows_with_timestamp)
                .execute()
            )

    def get_country(
        self,
        country: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:

        result = (
            supabase.table(self.TABLE)
            .select("country, symbol, company_name, exchange, overall_score, growth_score, quality_score, financial_strength_score, valuation_score, rank, generated_at")
            .eq("country", country)
            .order("rank")
            .limit(limit)
            .execute()
        )

        return result.data or []

    def get_latest_generated_at(self, country: str) -> Optional[str]:
        """Return the generated_at timestamp of the latest entry for a country."""
        result = (
            supabase.table(self.TABLE)
            .select("generated_at")
            .eq("country", country)
            .order("generated_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0].get("generated_at")