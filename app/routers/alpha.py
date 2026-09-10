
from typing import Optional

from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated_user
from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.repositories.portfolio_performance_repository import PortfolioPerformanceRepository
from app.repositories.alpha_history_repository import AlphaHistoryRepository
from app.core.supabase import supabase


router = APIRouter(
    prefix="/alpha",
    tags=["Alpha"],
    dependencies=[Depends(require_authenticated_user)],
)


alpha_repo = AlphaPortfolioRepository()
history_repo = AlphaHistoryRepository()
perf_repo = PortfolioPerformanceRepository()


@router.get("/india")
def india():
    """Return current India Alpha portfolio."""

    return alpha_repo.get_all("IN")


@router.get("/us")
def us():
    """Return current US Alpha portfolio."""

    return alpha_repo.get_all("US")


@router.get("/history")
def history(market: Optional[str] = None, limit: int = 200):
    """Return alpha history.

    Optional query params:
    - `market`: filter by market code (e.g. IN, US)
    - `limit`: number of rows to return (default 200)
    """

    if market:
        return history_repo.get_recent(market, limit=limit)

    # no market provided, return recent global history
    result = (
        supabase.table("alpha_history")
        .select("*")
        .order("performed_at", desc=True)
        .limit(limit)
        .execute()
    )

    return result.data or []


@router.get("/performance")
def performance(market: Optional[str] = None):
    """Return latest portfolio performance.

    If `market` is provided, return that market's latest snapshot. If
    omitted, return both India and USA snapshots.
    """

    if market:
        return perf_repo.get_latest(market)

    return {
        "india": perf_repo.get_latest("IN"),
        "usa": perf_repo.get_latest("US"),
    }
