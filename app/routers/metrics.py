from fastapi import APIRouter

from app.models.metrics import (
    FinancialStrengthMetrics,
    GrowthMetrics,
    MetricsResponse,
    QualityMetrics,
    ValuationMetrics,
)
from app.services.metrics_service import MetricsService
from app.services.yahoo_service import (
    MarketDataUnavailableError,
    SymbolNotFoundError,
    YahooService,
)

router = APIRouter()

yahoo = YahooService()


@router.get("/metrics/{symbol}")
async def get_metrics(symbol: str):
    try:
        financials = yahoo.get_financials(symbol)
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
        history = yahoo.get_financial_history(symbol)
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
