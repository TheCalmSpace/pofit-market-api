from fastapi import APIRouter, Depends

from app.models.metrics import (
    FinancialStrengthMetrics,
    GrowthMetrics,
    MetricsResponse,
    QualityMetrics,
    ValuationMetrics,
)
from app.dependencies import require_authenticated_user
from app.repositories.stock_repository import StockRepository
from app.services.metrics_service import MetricsService
from app.services.yahoo_service import (
    MarketDataUnavailableError,
    SymbolNotFoundError,
    YahooService,
)
from app.utils.symbol import resolve_yahoo_symbol

router = APIRouter(dependencies=[Depends(require_authenticated_user)])

yahoo = YahooService()
stock_repo = StockRepository()


@router.get("/metrics/{symbol}")
async def get_metrics(symbol: str):
    resolved_symbol = _resolve_symbol(symbol)

    try:
        financials = yahoo.get_financials(resolved_symbol)
    except SymbolNotFoundError:
        return MetricsResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason="Symbol not found.",
            growth=GrowthMetrics(),
            quality=QualityMetrics(),
            financial_strength=FinancialStrengthMetrics(),
            valuation=ValuationMetrics(),
        )
    except MarketDataUnavailableError as exc:
        return MetricsResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason=str(exc),
            growth=GrowthMetrics(),
            quality=QualityMetrics(),
            financial_strength=FinancialStrengthMetrics(),
            valuation=ValuationMetrics(),
        )

    try:
        history = yahoo.get_financial_history(resolved_symbol)
    except SymbolNotFoundError:
        return MetricsResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason="Symbol not found.",
            growth=GrowthMetrics(),
            quality=QualityMetrics(),
            financial_strength=FinancialStrengthMetrics(),
            valuation=ValuationMetrics(),
        )
    except MarketDataUnavailableError as exc:
        return MetricsResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason=str(exc),
            growth=GrowthMetrics(),
            quality=QualityMetrics(),
            financial_strength=FinancialStrengthMetrics(),
            valuation=ValuationMetrics(),
        )

    if financials.data_status != "available" or history.data_status != "available":
        missing = []
        if financials.data_status != "available":
            missing.append("financials")
        if history.data_status != "available":
            missing.append("financial_history")

        return MetricsResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason=f"Insufficient data: {', '.join(missing)} unavailable.",
            growth=GrowthMetrics(),
            quality=QualityMetrics(),
            financial_strength=FinancialStrengthMetrics(),
            valuation=ValuationMetrics(),
        )

    try:
        return MetricsService.build_metrics(
            history=history,
            financials=financials,
        )
    except Exception as exc:
        return MetricsResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason=f"Metrics calculation failed: {exc}",
            growth=GrowthMetrics(),
            quality=QualityMetrics(),
            financial_strength=FinancialStrengthMetrics(),
            valuation=ValuationMetrics(),
        )


def _resolve_symbol(symbol: str) -> str:
    try:
        stock = stock_repo.get_by_symbol(symbol.upper())
    except Exception:
        stock = None
    exchange = stock.get("exchange") if stock else None
    return resolve_yahoo_symbol(symbol.upper(), exchange)
