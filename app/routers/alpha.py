from typing import List, Optional

from fastapi import APIRouter, Query

from app.models import AlphaPerformanceReportResponse
from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.repositories.portfolio_performance_repository import PortfolioPerformanceRepository
from app.repositories.alpha_history_repository import AlphaHistoryRepository
from app.services.performance_service import PerformanceService
from app.core.supabase import supabase


router = APIRouter(
	prefix="/alpha",
	tags=["Alpha"],
)


router = APIRouter(
	prefix="/alpha",
	tags=["Alpha"],
)


alpha_repo = AlphaPortfolioRepository()
history_repo = AlphaHistoryRepository()
perf_repo = PortfolioPerformanceRepository()


@router.get("/india")
def india():
	return alpha_repo.get_all("IN")


@router.get("/us")
def us():
	return alpha_repo.get_all("US")


@router.get("/history")
def history(market: Optional[str] = None, limit: int = 200):
	if market:
		return history_repo.get_recent(market, limit=limit)
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
	if market:
		return perf_repo.get_latest(market)
	return {
		"india": perf_repo.get_latest("IN"),
		"usa": perf_repo.get_latest("US"),
	}


@router.get("/performance-report", response_model=AlphaPerformanceReportResponse)
def alpha_performance_report(
	market: str = Query(..., description="Market code: IN or US"),
	period: str = Query(
		...,
		description="Period: 1m, 6m, 1y, since-inception, or fy2025-26",
	),
):
	service = PerformanceService()
	return service.report_performance(market, period)