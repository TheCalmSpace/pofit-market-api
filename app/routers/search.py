from fastapi import APIRouter, HTTPException, Query, status

from app.models import ErrorResponse, SearchResponse
from app.services import InvalidMarketDataRequestError, MarketDataUnavailableError, YahooService

router = APIRouter(prefix="/search", tags=["Search"])
yahoo_service = YahooService()


@router.get(
    "",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def search_symbols(q: str = Query(..., min_length=1)) -> SearchResponse:
    try:
        return yahoo_service.search_symbols(q)
    except InvalidMarketDataRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except MarketDataUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
