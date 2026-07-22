from fastapi import APIRouter

from app.services.metrics_service import MetricsService
from app.services.score_service import ScoreService
from app.services.yahoo_service import YahooService

router = APIRouter()

yahoo = YahooService()
score_service = ScoreService()


@router.get("/score/{symbol}")
async def get_score(symbol: str):
    financials = yahoo.get_financials(symbol)
    history = yahoo.get_financial_history(symbol)

    metrics = MetricsService.build_metrics(
        history=history,
        financials=financials,
    )

    return score_service.build_score(metrics)