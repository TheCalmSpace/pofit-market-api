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
			date_start_iso = date_start.isoformat() if hasattr(date_start, "isoformat") else str(date_start)
			query = query.gte("as_of", date_start_iso)
		if date_end is not None:
			date_end_iso = date_end.isoformat() if hasattr(date_end, "isoformat") else str(date_end)
			query = query.lte("as_of", date_end_iso)
		result = query.order("as_of", desc=False).execute()
		return result.data or []