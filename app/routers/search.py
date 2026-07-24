from typing import Optional

from fastapi import APIRouter, Query

from app.repositories.stock_repository import StockRepository

router = APIRouter(
    prefix="/search",
    tags=["Search"],
)

stock_repo = StockRepository()


@router.get("")
def search_symbols(
    q: str = Query(..., min_length=1),
    country: Optional[str] = None,
):
    return stock_repo.search(
        query=q,
        country=country,
        limit=20,
    )