from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import require_authenticated_user
from app.models import ErrorResponse, HistoricalResponse
from app.repositories.stock_data_repository import StockDataRepository
from app.repositories.stock_repository import StockRepository
from app.services import (
    InvalidMarketDataRequestError,
    MarketDataUnavailableError,
    SymbolNotFoundError,
)
from app.services.market_data_service import MarketDataService
from app.services.yahoo_egress import create_yahoo_service
from app.utils.symbol import resolve_yahoo_symbol

router = APIRouter(
    prefix="/historical",
    tags=["Historical"],
    dependencies=[Depends(require_authenticated_user)],
)
yahoo_service = create_yahoo_service()
stock_repo = StockRepository()
stock_data_repo = StockDataRepository()
market_service = MarketDataService()


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
        # Try cache first - check if we have historical data in stock_data
        cached = stock_data_repo.get(symbol.upper())
        # Note: historical prices might be stored differently, try to refresh via market service
        try:
            stock = market_service.get_stock(symbol.upper())
        except Exception:
            stock = cached

        # Fallback to direct Yahoo call
        resolved = _resolve_symbol(symbol)
        try:
            result = yahoo_service.get_historical_prices(
                symbol=resolved,
                period=period,
                interval=interval,
            )
            return result
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
            # Try to serve from cache if available
            if cached:
                # Return a basic response structure if possible
                pass
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _resolve_symbol(symbol: str) -> str:
    stock = stock_repo.get_by_symbol_for_top_picks(symbol.upper())
    exchange = stock.get("exchange") if stock else None
    return resolve_yahoo_symbol(symbol.upper(), exchange)
