from fastapi import APIRouter, HTTPException, Query, status

from app.models import ErrorResponse, HistoricalResponse
from app.services import (
    InvalidMarketDataRequestError,
    MarketDataUnavailableError,
    SymbolNotFoundError,
    YahooService,
)

router = APIRouter(prefix="/historical", tags=["Historical"])
yahoo_service = YahooService()


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
    try:
        return yahoo_service.get_historical_prices(
            symbol=symbol,
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
