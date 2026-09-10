from typing import List, Dict, Any

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
            (
                supabase.table(self.TABLE)
                .insert(rows)
                .execute()
            )

    def get_country(
        self,
        country: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:

        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("country", country)
            .order("rank")
            .limit(limit)
            .execute()
        )

        return result.data or []