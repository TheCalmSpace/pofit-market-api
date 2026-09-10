from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import require_authenticated_user
from app.models import ErrorResponse, HistoricalResponse
from app.repositories.stock_repository import StockRepository
from app.services import (
    InvalidMarketDataRequestError,
    MarketDataUnavailableError,
    SymbolNotFoundError,
    YahooService,
)
from app.utils.symbol import resolve_yahoo_symbol

router = APIRouter(
    prefix="/historical",
    tags=["Historical"],
    dependencies=[Depends(require_authenticated_user)],
)
yahoo_service = YahooService()
stock_repo = StockRepository()


@router.get(
    "/{symbol}",
    response_model=HistoricalResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_historical_prices(
    symbol: str,
    period: str = Query("1mo", min_length=2),
    interval: str = Query("1d", min_length=2),
) -> HistoricalResponse:
    resolved = _resolve_symbol(symbol)
    try:
        return yahoo_service.get_historical_prices(
            symbol=resolved,
            period=period,
            interval=interval,
        )
    except InvalidMarketDataRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
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


def _resolve_symbol(symbol: str) -> str:
    stock = stock_repo.get_by_symbol(symbol.upper())
    exchange = stock.get("exchange") if stock else None
    return resolve_yahoo_symbol(symbol.upper(), exchange)
