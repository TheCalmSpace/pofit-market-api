from fastapi import APIRouter, HTTPException, status

from app.models import ErrorResponse, FinancialsResponse
from app.services import MarketDataUnavailableError, SymbolNotFoundError, YahooService

router = APIRouter(prefix="/financials", tags=["Financials"])
yahoo_service = YahooService()


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
        return yahoo_service.get_financials(symbol)
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
