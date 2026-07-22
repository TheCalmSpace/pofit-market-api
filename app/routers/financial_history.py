from fastapi import APIRouter, HTTPException, status

from app.models import ErrorResponse, FinancialHistoryResponse
from app.services import MarketDataUnavailableError, SymbolNotFoundError, YahooService

router = APIRouter(prefix="/financial-history", tags=["Financial History"])
yahoo_service = YahooService()


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
        return yahoo_service.get_financial_history(symbol)
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
