from fastapi import APIRouter, Depends, Query
from typing import Any, Dict, List, Optional

from app.dependencies import require_authenticated_user
from app.repositories.stock_repository import StockRepository

router = APIRouter(
    prefix="/radar",
    tags=["Radar"],
    dependencies=[Depends(require_authenticated_user)],
)

stock_repo = StockRepository()


def _unwrap_query_default(value: Any) -> Any:
    if hasattr(value, "default"):
        return value.default
    return value


@router.get("/stocks")
def get_radar_stocks(
    status: Optional[str] = Query(
        default=None,
        description="Filter by radar status (e.g. NEWLY_LISTED, DATA_COLLECTION, INSUFFICIENT_HISTORY, REVIEW_REQUIRED, ELIGIBLE, INACTIVE)",
    ),
    exchange: Optional[str] = Query(
        default=None,
        description="Filter by exchange (e.g. NSE, BSE, NASDAQ)",
    ),
    limit: int = Query(100, ge=1, le=1000),
) -> List[dict]:
    """
    Retrieve securities currently requiring monitoring.

    This endpoint powers the Radar view in the Main Pofit dashboard.
    It is read-only and does not modify any data.
    """
    status = _unwrap_query_default(status)
    exchange = _unwrap_query_default(exchange)

    if status:
        stocks = stock_repo.list_by_status(
            status=status,
            exchange=exchange,
            limit=limit,
        )
    else:
        stocks = stock_repo.list_all_by_country("IN", limit=limit)

    results = []
    for stock in stocks:
        item = {
            "symbol": stock.get("symbol"),
            "company_name": stock.get("company_name"),
            "exchange": stock.get("exchange"),
            "isin": stock.get("isin"),
            "status": stock.get("status", "ACTIVE"),
            "first_listed_date": stock.get("first_listed_date"),
            "last_synced_at": stock.get("last_synced_at"),
        }
        results.append(item)

    return results[:limit]
