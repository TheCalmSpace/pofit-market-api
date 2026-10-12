from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import require_internal_or_user_auth
from app.models import ErrorResponse, QuoteResponse, HistoricalResponse, FinancialsResponse, FinancialHistoryResponse
from app.repositories.stock_repository import StockRepository
from app.services import MarketDataUnavailableError, SymbolNotFoundError
from app.services.yahoo_egress import create_yahoo_service
from app.utils.symbol import resolve_yahoo_symbol

router = APIRouter(
    prefix="/internal",
    tags=["Internal"],
    dependencies=[Depends(require_internal_or_user_auth)],
)

yahoo_service = create_yahoo_service()
stock_repo = StockRepository()


def _resolve_symbol(symbol: str) -> str:
    stock = stock_repo.get_by_symbol_for_top_picks(symbol.upper())
    exchange = stock.get("exchange") if stock else None
    return resolve_yahoo_symbol(symbol.upper(), exchange)


@router.get(
    "/quote/{symbol}",
    response_model=QuoteResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_internal_quote(symbol: str) -> QuoteResponse:
    try:
        return yahoo_service.get_stock(_resolve_symbol(symbol))
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


@router.get(
    "/historical/{symbol}",
    response_model=HistoricalResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_internal_historical(
    symbol: str,
    period: str = "1mo",
    interval: str = "1d",
) -> HistoricalResponse:
    try:
        return yahoo_service.get_historical_prices(
            _resolve_symbol(symbol), period=period, interval=interval
        )
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


@router.get(
    "/financials/{symbol}",
    response_model=FinancialsResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_internal_financials(symbol: str) -> FinancialsResponse:
    try:
        return yahoo_service.get_financials(_resolve_symbol(symbol))
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


@router.get(
    "/financial-history/{symbol}",
    response_model=FinancialHistoryResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_internal_financial_history(symbol: str) -> FinancialHistoryResponse:
    try:
        return yahoo_service.get_financial_history(_resolve_symbol(symbol))
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