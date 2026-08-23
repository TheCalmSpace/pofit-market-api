from fastapi import APIRouter, HTTPException, status

from app.models import ErrorResponse, FinancialHistoryResponse
from app.repositories.stock_repository import StockRepository
from app.services import MarketDataUnavailableError, SymbolNotFoundError, YahooService
from app.utils.symbol import resolve_yahoo_symbol

router = APIRouter(prefix="/financial-history", tags=["Financial History"])
yahoo_service = YahooService()
stock_repo = StockRepository()


@router.get(
    "/{symbol}",
    response_model=FinancialHistoryResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_financial_history(symbol: str) -> FinancialHistoryResponse:
    resolved = _resolve_symbol(symbol)
    try:
        return yahoo_service.get_financial_history(resolved)
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
