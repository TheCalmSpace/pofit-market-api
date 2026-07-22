from fastapi import APIRouter, HTTPException, status

from app.models import ErrorResponse, QuoteResponse
from app.services import MarketDataUnavailableError, SymbolNotFoundError, YahooService

router = APIRouter(prefix="/quote", tags=["Quote"])
yahoo_service = YahooService()


@router.get(
    "/{symbol}",
    response_model=QuoteResponse,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_quote(symbol: str) -> QuoteResponse:
    try:
        return yahoo_service.get_quote(symbol)
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
