from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import require_authenticated_user
from app.models import ErrorResponse, FinancialsResponse
from app.repositories.stock_repository import StockRepository
from app.services import MarketDataUnavailableError, SymbolNotFoundError, YahooService
from app.utils.symbol import resolve_yahoo_symbol

router = APIRouter(
    prefix="/financials",
    tags=["Financials"],
    dependencies=[Depends(require_authenticated_user)],
)
yahoo_service = YahooService()
stock_repo = StockRepository()


@router.get(
    "/{symbol}",
    response_model=FinancialsResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_financials(symbol: str) -> FinancialsResponse:
    resolved = _resolve_symbol(symbol)
    try:
        return yahoo_service.get_financials(resolved)
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
