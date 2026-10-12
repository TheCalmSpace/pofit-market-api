from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase import supabase


class PortfolioPerformanceRepository:
    TABLE = "portfolio_performance"

    def insert_snapshot(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        market = (snapshot.get("market") or "").upper()
        data = {**snapshot, "market": market}
        if not data.get("as_of"):
            data["as_of"] = datetime.now(timezone.utc).isoformat()
        if not data.get("date"):
            data["date"] = self._derive_date(data["as_of"])
        if not data.get("period"):
            data["period"] = "INCEPTION"
        result = supabase.table(self.TABLE).insert(data).execute()
        if not result.data:
            return None
        return result.data[0]

    @staticmethod
    def _derive_date(as_of: Any) -> str:
        """Return the calendar date of `as_of` as YYYY-MM-DD.

        `portfolio_performance.date` is NOT NULL in production. The
        application reads and writes `as_of`, so `date` is derived from it
        here rather than being stored independently. The column is neither
        dropped nor made nullable, and the schema is left unchanged.
        """
        if isinstance(as_of, datetime):
            return as_of.date().isoformat()
        if isinstance(as_of, date):
            return as_of.isoformat()
        text = str(as_of).strip()
        # Handles both plain dates and ISO-8601 timestamps.
        return text.split("T", 1)[0]

    def get_latest(self, market: str) -> Optional[Dict[str, Any]]:
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
        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", (market or "").upper())
            .order("as_of", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    def get_for_period(
        self,
        market: str,
        columns: List[str],
        date_start: Optional[date] = None,
        date_end: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        market = (market or "").upper()
        query = (
            supabase.table(self.TABLE)
            .select(",".join(columns))
            .eq("market", market)
        )
        if date_start is not None:
            date_start_iso = (
                date_start.isoformat()
                if hasattr(date_start, "isoformat")
                else str(date_start)
            )
            query = query.gte("as_of", date_start_iso)
        if date_end is not None:
            date_end_iso = (
                date_end.isoformat()
                if hasattr(date_end, "isoformat")
                else str(date_end)
            )
            query = query.lte("as_of", date_end_iso)
        result = query.order("as_of", desc=False).execute()
        return result.data or []

    # ------------------------------------------------------------------
    # Historical backfill reconciliation helpers.
    #
    # The production schema enforces uniqueness on (market, period,
    # as_of) only. It does NOT enforce uniqueness on (market, date),
    # which is why duplicate rows can share a market and date while
    # differing in as_of. These helpers support an idempotent,
    # repository-level reconciliation that keeps exactly one row per
    # (market, date, period) without requiring a schema migration.
    # ------------------------------------------------------------------

    def get_rows_for_date(
        self,
        market: str,
        snapshot_date: str,
        period: str = "INCEPTION",
    ) -> List[Dict[str, Any]]:
        """Return every row for a (market, date, period), oldest first.

        Ordered by ``as_of`` ascending so the caller can deterministically
        pick the most recent row as the canonical row to update and treat
        the remaining rows as duplicates to remove.
        """
        market = (market or "").upper()
        snapshot_date = (snapshot_date or "").strip()[:10]
        result = (
            supabase.table(self.TABLE)
            .select("*")
            .eq("market", market)
            .eq("date", snapshot_date)
            .eq("period", period)
            .order("as_of", desc=False)
            .execute()
        )
        return result.data or []

    def update_snapshot_by_id(
        self,
        snapshot_id: Any,
        values: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update the value columns of a single row by primary key.

        ``id`` and ``market`` are never modified here; only the
        reconstructed performance columns are written.
        """
        if snapshot_id is None:
            return None
        payload = dict(values)
        payload.pop("id", None)
        payload.pop("market", None)
        if not payload:
            return None
        result = (
            supabase.table(self.TABLE)
            .update(payload)
            .eq("id", snapshot_id)
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]

    def delete_by_id(self, snapshot_id: Any) -> bool:
        """Delete a single row by primary key. Returns True if a row was removed."""
        if snapshot_id is None:
            return False
        result = (
            supabase.table(self.TABLE)
            .delete()
            .eq("id", snapshot_id)
            .execute()
        )
        return bool(getattr(result, "data", None))

    def delete_by_ids(self, snapshot_ids: List[Any]) -> int:
        """Delete several rows by primary key. Returns the count removed."""
        removed = 0
        for snapshot_id in snapshot_ids:
            if self.delete_by_id(snapshot_id):
                removed += 1
        return removed

    def apply_reconciliation_plan(
        self,
        actions: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Apply a validated backfill plan atomically through a database RPC."""
        expected = {"inserted": 0, "updated": 0, "deleted": 0}
        for action in actions:
            kind = action.get("kind")
            if kind == "INSERT":
                expected["inserted"] += 1
            elif kind == "UPDATE":
                expected["updated"] += 1
                expected["deleted"] += len(action.get("deleted_ids") or [])
                if (action.get("existing") or {}).get("id") is None:
                    raise ValueError("UPDATE action is missing the existing row id.")
            elif kind == "DELETE":
                expected["deleted"] += len(action.get("deleted_ids") or [])
            else:
                raise ValueError(
                    "Unsupported reconciliation action %r." % kind
                )

        if not actions:
            return expected

        result = (
            supabase.rpc(
                "apply_alpha_performance_reconciliation",
                {"p_actions": actions},
            )
            .execute()
        )
        rows = getattr(result, "data", None)
        if not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError(
                "Alpha performance reconciliation RPC returned no single "
                "verification row."
            )

        actual = rows[0]
        if not isinstance(actual, dict):
            raise RuntimeError(
                "Alpha performance reconciliation RPC returned an invalid "
                "verification row."
            )
        verified: Dict[str, int] = {}
        for key in expected:
            value = actual.get(key)
            if type(value) is not int:
                raise RuntimeError(
                    "Alpha performance reconciliation RPC omitted its %s "
                    "count." % key
                )
            verified[key] = value
        if verified != expected:
            raise RuntimeError(
                "Alpha performance reconciliation count mismatch: "
                "expected %r, received %r." % (expected, verified)
            )
        return verified
