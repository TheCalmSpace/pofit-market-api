from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.repositories.stock_repository import StockRepository
from app.repositories.stock_data_repository import StockDataRepository

from app.services.yahoo_service import (
    YahooService,
    SymbolNotFoundError,
)
from app.services.metrics_service import MetricsService
from app.services.score_service import ScoreService

from app.utils.model_utils import to_dict


CACHE_TTL = timedelta(hours=6)


class MarketDataService:

    def __init__(self):
        self.stock_repo = StockRepository()
        self.stock_data_repo = StockDataRepository()

        self.yahoo = YahooService()
        self.metrics = MetricsService()
        self.score = ScoreService()

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

    def refresh_stock(
        self,
        symbol: str,
    ) -> Dict[str, Any]:

        symbol = symbol.upper()

        quote = self.yahoo.get_quote(symbol)

        financials = self.yahoo.get_financials(symbol)

        history = self.yahoo.get_financial_history(symbol)

        self.stock_repo.update_metadata(
            symbol=symbol,
            sector=financials.sector,
            industry=financials.industry,
        )

        metrics = self.metrics.build_metrics(
            history,
            financials,
        )

        score = self.score.build_score(metrics)

        payload = {
            "quote_json": to_dict(quote),
            "metrics_json": to_dict(metrics),
            "score_json": to_dict(score),
            "cache_status": "fresh",
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

        if cached.get("cache_status") != "fresh":
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