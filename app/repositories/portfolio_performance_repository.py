from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase import supabase


class PortfolioPerformanceRepository:
    """Repository for `portfolio_performance` table.

    Provides simple CRUD-like reads for performance snapshots. This
    repository follows the project's existing Supabase access pattern
    and intentionally contains no business logic.
    """

    TABLE = "portfolio_performance"

    def insert_snapshot(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a daily performance snapshot.

        The `snapshot` dictionary should include at least a `market`
        key. The repository normalizes `market` to upper-case and
        ensures an `as_of` timestamp exists (UTC ISO). Returns the
        inserted row on success or `None` on failure.
        """

        market = (snapshot.get("market") or "").upper()

        data = {**snapshot, "market": market}

        if not data.get("as_of"):
            data["as_of"] = datetime.now(timezone.utc).isoformat()

        result = supabase.table(self.TABLE).insert(data).execute()

        if not result.data:
            return None

        return result.data[0]

    def get_latest(self, market: str) -> Optional[Dict[str, Any]]:
        """Return the latest snapshot for `market` (newest `as_of`)."""

        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", (market or "").upper())
            .order("as_of", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    def get_history(self, market: str, limit: int = 365) -> List[Dict[str, Any]]:
        """Return recent snapshots for `market`, newest first.

        Results are ordered by `as_of` descending and limited by
        `limit`.
        """

        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", (market or "").upper())
            .order("as_of", desc=True)
            .limit(limit)
            .execute()
        )

        return result.data or []

    def get_by_date(self, market: str, date: Any) -> Optional[Dict[str, Any]]:
        """Return a single snapshot for `market` matching `date`.

        `date` may be a `str`, `date` or `datetime`. The repository
        compares using the ISO string representation.
        """

        date_iso = None

        if hasattr(date, "isoformat"):
            date_iso = date.isoformat()
        else:
            date_iso = str(date)

        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", (market or "").upper())
            .eq("as_of", date_iso)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

