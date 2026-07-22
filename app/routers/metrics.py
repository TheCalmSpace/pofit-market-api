from fastapi import APIRouter

from app.services.metrics_service import MetricsService
from app.services.yahoo_service import YahooService

router = APIRouter()

yahoo = YahooService()


@router.get("/metrics/{symbol}")
async def get_metrics(symbol: str):
    financials = yahoo.get_financials(symbol)
    history = yahoo.get_financial_history(symbol)

    return MetricsService.build_metrics(
        history=history,
        financials=financials,
    )