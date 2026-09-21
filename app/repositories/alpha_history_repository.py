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
		result = (
			supabase.table(self.TABLE)
			.select("market, symbol, action, reason, price, score, company_name, performed_at")
			.eq("market", (market or "").upper())
			.order("performed_at", desc=True)
			.limit(limit)
			.execute()
		)

		return result.data or []

	def get_by_symbol(self, market: str, symbol: str) -> List[Dict[str, Any]]:
		result = (
			supabase.table(self.TABLE)
			.select("market, symbol, action, reason, price, score, company_name, performed_at")
			.eq("market", (market or "").upper())
			.eq("symbol", (symbol or "").upper())
			.order("performed_at", desc=True)
			.execute()
		)

		return result.data or []

	def get_events_up_to(
		self, market: str, as_of: str
	) -> List[Dict[str, Any]]:
		result = (
			supabase.table(self.TABLE)
			.select("*")
			.eq("market", (market or "").upper())
			.lte("performed_at", as_of)
			.order("performed_at", asc=True)
			.execute()
		)

		return result.data or []