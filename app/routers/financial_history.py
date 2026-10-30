from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import require_authenticated_user
from app.models import ErrorResponse, FinancialHistoryResponse
from app.repositories.stock_data_repository import StockDataRepository
from app.repositories.stock_repository import StockRepository
from app.services import MarketDataUnavailableError, SymbolNotFoundError
from app.services.market_data_service import MarketDataService
from app.services.yahoo_egress import create_yahoo_service
from app.utils.symbol import resolve_yahoo_symbol

router = APIRouter(
    prefix="/financial-history",
    tags=["Financial History"],
    dependencies=[Depends(require_authenticated_user)],
)
yahoo_service = create_yahoo_service()
stock_repo = StockRepository()
stock_data_repo = StockDataRepository()
market_service = MarketDataService()


@router.get(
    "/{symbol}",
    response_model=FinancialHistoryResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_financial_history(symbol: str) -> FinancialHistoryResponse:
    try:
        # Try cache first
        cached = stock_data_repo.get(symbol.upper())
        if cached and cached.get("financial_history_json") is not None:
            try:
                return FinancialHistoryResponse(**cached["financial_history_json"])
            except Exception:
                pass

        # Try to refresh via market service
        try:
            stock = market_service.get_stock(symbol.upper())
            if stock and stock.get("financial_history_json") is not None:
                try:
                    return FinancialHistoryResponse(**stock["financial_history_json"])
                except Exception:
                    pass
        except Exception:
            pass

        # Fallback to direct Yahoo call
        resolved = _resolve_symbol(symbol)
        try:
            result = yahoo_service.get_financial_history(resolved)
            return result
        except SymbolNotFoundError as exc:
            if cached and cached.get("financial_history_json") is not None:
                try:
                    return FinancialHistoryResponse(**cached["financial_history_json"])
                except Exception:
                    pass
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except MarketDataUnavailableError as exc:
            if cached and cached.get("financial_history_json") is not None:
                try:
                    return FinancialHistoryResponse(**cached["financial_history_json"])
                except Exception:
                    pass
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        cached = stock_data_repo.get(symbol.upper())
        if cached and cached.get("financial_history_json") is not None:
            try:
                return FinancialHistoryResponse(**cached["financial_history_json"])
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _resolve_symbol(symbol: str) -> str:
    stock = stock_repo.get_by_symbol_for_top_picks(symbol.upper())
    exchange = stock.get("exchange") if stock else None
    return resolve_yahoo_symbol(symbol.upper(), exchange)
