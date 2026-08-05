from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase import supabase


class AlphaHistoryRepository:
    """Repository for reading and writing `alpha_history` events.

    This class encapsulates all Supabase interactions for the
    `alpha_history` table. It performs simple normalization of inputs
    (upper-casing keys) and provides helpers to insert and query
    history rows.
    """

    TABLE = "alpha_history"

    # Action constants to avoid magic strings
    ACTION_ADD = "ADD"
    ACTION_REMOVE = "REMOVE"

    def insert_event(
        self,
        market: str,
        symbol: str,
        action: str,
        reason: str,
        price: Optional[float],
        score: Optional[float],
        company_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert a single history event into the `alpha_history` table.

        The repository writes the following columns:
        - market (upper-cased)
        - symbol (upper-cased)
        - company_name
        - action (upper-cased)
        - reason
        - price
        - score
        - performed_at (UTC ISO timestamp)

        Returns the inserted row on success or `None` on failure.
        """

        row = {
            "market": (market or "").upper(),
            "symbol": (symbol or "").upper(),
            "company_name": company_name,
            "action": (action or "").upper(),
            "reason": reason,
            "price": price,
            "score": score,
            "performed_at": datetime.now(timezone.utc).isoformat(),
        }

        result = supabase.table(self.TABLE).insert(row).execute()

        if not result.data:
            return None

        return result.data[0]

    def get_recent(self, market: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the most recent history events for a market.

        Results are ordered by `performed_at` descending and limited by
        `limit`.
        """

        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", (market or "").upper())
            .order("performed_at", desc=True)
            .limit(limit)
            .execute()
        )

        return result.data or []

    def get_by_symbol(self, market: str, symbol: str) -> List[Dict[str, Any]]:
        """Return the full history for a single symbol in a market.

        Results are ordered newest first (performed_at desc).
        """

        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", (market or "").upper())
            .eq("symbol", (symbol or "").upper())
            .order("performed_at", desc=True)
            .execute()
        )

        return result.data or []

