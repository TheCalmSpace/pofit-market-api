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


def _build_performance_summary(market: str) -> dict:
	"""Build the Alpha performance payload from stored NAV snapshots.

	Returns are derived with the canonical `report_performance`
	calculation over the stored NAV series, then converted from a
	fraction to a percentage because the Alpha page renders these fields
	with a `%` suffix and does not rescale them.

	Reads are database-only. When fewer than two snapshots exist the
	canonical calculation reports `not_available` and every return stays
	None, so the page can show an explicit "building history" state
	instead of a fabricated figure.
	"""
	market = (market or "").upper()
	service = PerformanceService()
	report = service.report_performance(market, "since-inception")
	snapshot = perf_repo.get_latest(market) or {}

	portfolio_pct = report.get("portfolio_return")
	benchmark_pct = report.get("benchmark_return")

	if portfolio_pct is not None:
		portfolio_pct = portfolio_pct * 100.0
	if benchmark_pct is not None:
		benchmark_pct = benchmark_pct * 100.0

	alpha = (
		portfolio_pct - benchmark_pct
		if portfolio_pct is not None and benchmark_pct is not None
		else None
	)

	return {
		"market": market,
		"period": report.get("period"),
		"status": report.get("status"),
		"portfolio_return": portfolio_pct,
		"benchmark_return": benchmark_pct,
		"alpha": alpha,
		"portfolio_nav": snapshot.get("portfolio_nav"),
		"benchmark_nav": snapshot.get("benchmark_nav"),
		"as_of": snapshot.get("as_of") or report.get("as_of"),
		"date": snapshot.get("date"),
		"holdings_count": snapshot.get("holdings_count") or 0,
		"benchmark": report.get("benchmark"),
		"reason": report.get("reason"),
	}


@router.get("/performance")
def performance(market: Optional[str] = None):
	if market:
		return _build_performance_summary(market)
	return {
		"india": _build_performance_summary("IN"),
		"usa": _build_performance_summary("US"),
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