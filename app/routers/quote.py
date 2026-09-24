from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import require_authenticated_user
from app.models import ErrorResponse, QuoteResponse
from app.repositories.stock_data_repository import StockDataRepository
from app.services.market_data_service import MarketDataService
from app.services.yahoo_service import (
    MarketDataUnavailableError,
    SymbolNotFoundError,
)

router = APIRouter(
    prefix="/quote",
    tags=["Quote"],
    dependencies=[Depends(require_authenticated_user)],
)

market_service = MarketDataService()
stock_data_repo = StockDataRepository()


@router.get(
    "/{symbol}",
    response_model=QuoteResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_quote(symbol: str) -> QuoteResponse:
    try:
        cached = stock_data_repo.get_quote_data(symbol)

        if cached and market_service._is_cache_valid(cached):
            return QuoteResponse(**cached["quote_json"])

        stock = market_service.get_stock(symbol)

        return QuoteResponse(**stock["quote_json"])

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