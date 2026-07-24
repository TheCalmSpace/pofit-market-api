from fastapi import APIRouter, HTTPException, status

from app.models.score import ScoreResponse

from app.services.market_data_service import MarketDataService
from app.services.yahoo_service import (
    MarketDataUnavailableError,
    SymbolNotFoundError,
)

router = APIRouter()

market_service = MarketDataService()


@router.get(
    "/score/{symbol}",
    response_model=ScoreResponse,
)
def get_score(symbol: str):

    try:

        stock = market_service.get_stock(symbol)

        return ScoreResponse(**stock["score_json"])

    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except MarketDataUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc