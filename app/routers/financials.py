from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import require_authenticated_user
from app.models import ErrorResponse, FinancialsResponse
from app.repositories.stock_data_repository import StockDataRepository
from app.repositories.stock_repository import StockRepository
from app.services import MarketDataUnavailableError, SymbolNotFoundError
from app.services.market_data_service import MarketDataService
from app.services.yahoo_egress import create_yahoo_service
from app.utils.symbol import resolve_yahoo_symbol
from app.utils.model_utils import to_dict

router = APIRouter(
    prefix="/financials",
    tags=["Financials"],
    dependencies=[Depends(require_authenticated_user)],
)
yahoo_service = create_yahoo_service()
stock_repo = StockRepository()
stock_data_repo = StockDataRepository()
market_service = MarketDataService()


@router.get(
    "/{symbol}",
    response_model=FinancialsResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_financials(symbol: str) -> FinancialsResponse:
    try:
        # Try cache first
        try:
            cached = stock_data_repo.get(symbol.upper())
        except Exception:
            cached = None
        if cached and cached.get("financials_json") is not None:
            try:
                return FinancialsResponse(**cached["financials_json"])
            except Exception:
                pass

        # Try to refresh via market service
        try:
            stock = market_service.get_stock(symbol.upper())
            if stock and stock.get("financials_json") is not None:
                try:
                    return FinancialsResponse(**stock["financials_json"])
                except Exception:
                    pass
        except SymbolNotFoundError:
            raise
        except MarketDataUnavailableError:
            raise
        except HTTPException:
            raise
        except Exception:
            pass

        # Fallback to direct Yahoo call
        try:
            resolved = _resolve_symbol(symbol)
        except Exception:
            resolved = symbol.upper()
        try:
            result = yahoo_service.get_financials(resolved)
            return result
        except SymbolNotFoundError as exc:
            # If we have cached data, return it even if stale
            try:
                cached_fallback = stock_data_repo.get(symbol.upper())
            except Exception:
                cached_fallback = None
            if cached_fallback and cached_fallback.get("financials_json") is not None:
                try:
                    return FinancialsResponse(**cached_fallback["financials_json"])
                except Exception:
                    pass
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except MarketDataUnavailableError as exc:
            try:
                cached_fallback = stock_data_repo.get(symbol.upper())
            except Exception:
                cached_fallback = None
            if cached_fallback and cached_fallback.get("financials_json") is not None:
                try:
                    return FinancialsResponse(**cached_fallback["financials_json"])
                except Exception:
                    pass
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
    except HTTPException:
        raise
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MarketDataUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        try:
            cached_fallback = stock_data_repo.get(symbol.upper())
        except Exception:
            cached_fallback = None
        if cached_fallback and cached_fallback.get("financials_json") is not None:
            try:
                return FinancialsResponse(**cached_fallback["financials_json"])
            except Exception:
                pass
        # Check if it's a symbol not found
        if 'not found' in str(exc).lower() or 'symbolnotfound' in str(type(exc).__name__).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _resolve_symbol(symbol: str) -> str:
    stock = stock_repo.get_by_symbol_for_top_picks(symbol.upper())
    exchange = stock.get("exchange") if stock else None
    return resolve_yahoo_symbol(symbol.upper(), exchange)
