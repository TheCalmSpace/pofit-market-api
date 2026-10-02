from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.core.supabase import supabase
from app.repositories.stock_repository import StockRepository
from app.repositories.stock_data_repository import StockDataRepository

from app.services.yahoo_service import YahooService, SymbolNotFoundError
from app.services.metrics_service import MetricsService
from app.services.score_service import ScoreService
from app.services.explanation_service import ExplanationService
from app.services.eligibility_service import EligibilityService

from app.utils.model_utils import to_dict


CACHE_TTL = timedelta(hours=6)


class MarketDataService:

    def __init__(self):
        self.stock_repo = StockRepository()
        self.stock_data_repo = StockDataRepository()

        self.yahoo = YahooService()
        self.metrics = MetricsService()
        self.score = ScoreService()
        self.explanation = ExplanationService()
        self.eligibility = EligibilityService()

    def get_stock(
        self,
        symbol: str,
    ) -> Dict[str, Any]:

        symbol = symbol.upper()

        stock = self.stock_repo.get_by_symbol(symbol)

        if stock is None:
            raise SymbolNotFoundError(symbol)

        cached = self.stock_data_repo.get(symbol)

        if self._is_cache_valid(cached):
            return cached

        return self.refresh_stock(symbol)

    def get_stock_for_top_picks(
        self,
        symbol: str,
    ) -> Dict[str, Any]:
        symbol = symbol.upper()

        stock = self.stock_repo.get_by_symbol_for_top_picks(symbol)

        if stock is None:
            raise SymbolNotFoundError(symbol)

        cached = self.stock_data_repo.get_for_top_picks(symbol)

        if self._is_cache_valid(cached):
            return cached

        return self.refresh_stock(symbol, stock=stock)

    def _yahoo_symbol_for(self, symbol: str, exchange: str) -> str:
        yahoo_symbol = symbol

        if "." not in yahoo_symbol:
            if exchange == "NSE":
                yahoo_symbol = f"{symbol}.NS"
            elif exchange == "BSE":
                yahoo_symbol = f"{symbol}.BO"

        return yahoo_symbol

    def refresh_quote(
        self,
        symbol: str,
        stock: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update only the cached quote for a symbol.

        Metrics, score, eligibility and explanation are all derived from
        fundamentals and price history, so a quote-only update leaves every
        derived value exactly as the previous full refresh computed it. This
        keeps prices current for the handful of symbols that can actually
        enter a portfolio without re-downloading the full financial history.
        """

        symbol = symbol.upper()

        if stock is None:
            stock = self.stock_repo.get_by_symbol(symbol)

        if stock is None:
            raise SymbolNotFoundError(symbol)

        exchange = (stock.get("exchange") or "").upper()
        yahoo_symbol = self._yahoo_symbol_for(symbol, exchange)

        quote = self.yahoo.get_quote(yahoo_symbol)

        # Preserve whatever the previous full refresh stored. Only the quote and
        # the freshness marker change; every derived value keeps the value the
        # last full refresh computed for it.
        payload = {
            **self._stored_payload(symbol),
            "quote_json": to_dict(quote),
            "updated_at": datetime.utcnow().isoformat(),
        }

        self.stock_data_repo.save(symbol, payload)

        return payload

    def _stored_payload(self, symbol: str) -> Dict[str, Any]:
        """Return the currently stored JSON columns for a symbol."""

        result = (
            supabase.table("stock_data")
            .select(
                "profile_json, quote_json, income_json, balance_json, "
                "cashflow_json, metrics_json, ratios_json, score_json, "
                "cache_status, eligibility_json, explanation_json, "
                "financial_history_json, financials_json"
            )
            .eq("symbol", symbol.upper())
            .limit(1)
            .execute()
        )

        if not result.data:
            return {}

        return dict(result.data[0])

    def refresh_stock(
        self,
        symbol: str,
        stock: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        symbol = symbol.upper()

        if stock is None:
            stock = self.stock_repo.get_by_symbol(symbol)

        if stock is None:
            raise SymbolNotFoundError(symbol)

        exchange = (stock.get("exchange") or "").upper()

        yahoo_symbol = self._yahoo_symbol_for(symbol, exchange)

        print("=" * 80)
        print(f"DB SYMBOL    : {symbol}")
        print(f"EXCHANGE     : {exchange}")
        print(f"YAHOO SYMBOL : {yahoo_symbol}")
        print("=" * 80)

        quote = self.yahoo.get_quote(yahoo_symbol)

        financials = self.yahoo.get_financials(yahoo_symbol)

        history = self.yahoo.get_financial_history(yahoo_symbol)

        self.stock_repo.update_metadata(
            symbol=symbol,
            sector=financials.sector,
            industry=financials.industry,
        )

        eligibility = self.eligibility.build(
            quote=quote,
            financials=financials,
        )

        required_financial_data_available = (
            financials.data_status == "available"
            and history.data_status == "available"
        )

        metrics = None
        score = None
        explanation = None
        if required_financial_data_available:
            metrics = self.metrics.build_metrics(history, financials)
            score = self.score.build_score(metrics)
            explanation = self.explanation.build_explanation(score)

        payload = {
            "quote_json": to_dict(quote),
            "metrics_json": to_dict(metrics) if metrics else None,
            "score_json": to_dict(score) if score else None,
            "eligibility_json": to_dict(eligibility),
            "explanation_json": to_dict(explanation) if explanation else None,
            "financials_json": to_dict(financials),
            "financial_history_json": to_dict(history),
            "cache_status": "fresh" if required_financial_data_available else "partial",
            "updated_at": datetime.utcnow().isoformat(),
        }

        self.stock_data_repo.save(
            symbol,
            payload,
        )

        return payload

    def _is_cache_valid(
        self,
        cached: Optional[Dict[str, Any]],
    ) -> bool:

        if cached is None:
            return False

        if cached.get("cache_status") not in {"fresh", "partial"}:
            return False

        updated = cached.get("updated_at")

        if updated is None:
            return False

        try:
            updated = datetime.fromisoformat(
                updated.replace("Z", "+00:00")
            )
        except Exception:
            return False

        updated = updated.replace(tzinfo=None)

        return (
            datetime.utcnow() - updated
        ) < CACHE_TTL
