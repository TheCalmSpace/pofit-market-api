import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.daily_top_picks_service import DailyTopPicksService
from app.services.alpha_portfolio_service import AlphaPortfolioService
from app.services.performance_service import PerformanceService


scheduler = BackgroundScheduler(
	timezone=ZoneInfo("Asia/Kolkata")
)
_scheduler = scheduler
_scheduler_started = False
logger = logging.getLogger(__name__)


def generate_india():
	print("=" * 80)
	print("POFIT Scheduler")
	print("Generating India Top Picks...")
	print("=" * 80)
	service = DailyTopPicksService()
	result = service.generate_country("IN")
	print(f"Generated {len(result)} India Top Picks")

	alpha_service = AlphaPortfolioService()

	try:
		count = alpha_service.reconcile_market("IN")
		print(f"Alpha portfolio updated; holdings={count} (IN)")
	except Exception:
		logger.exception("Alpha portfolio update failed for IN")

	try:
		perf_service = PerformanceService()
		perf_service.snapshot_market("IN")
		print("Performance snapshot stored (IN)")
	except Exception:
		logger.exception("Performance snapshot failed for IN")


def generate_usa():
	print("=" * 80)
	print("POFIT Scheduler")
	print("Generating USA Top Picks...")
	print("=" * 80)
	service = DailyTopPicksService()
	result = service.generate_country("US")
	print(f"Generated {len(result)} USA Top Picks")

	alpha_service = AlphaPortfolioService()

	try:
		count = alpha_service.reconcile_market("US")
		print(f"Alpha portfolio updated; holdings={count} (US)")
	except Exception:
		logger.exception("Alpha portfolio update failed for US")

	try:
		perf_service = PerformanceService()
		perf_service.snapshot_market("US")
		print("Performance snapshot stored (US)")
	except Exception:
		logger.exception("Performance snapshot failed for US")


def start_scheduler():
	global _scheduler_started

	if scheduler.running:
		return scheduler

	scheduler.add_job(
		generate_india,
		trigger="cron",
		hour=8,
		minute=0,
		id="india_top_picks",
		replace_existing=True,
		coalesce=True,
		max_instances=1,
	)

	scheduler.add_job(
		generate_usa,
		trigger="cron",
		hour=9,
		minute=30,
		id="usa_top_picks",
		replace_existing=True,
		coalesce=True,
		max_instances=1,
	)

	scheduler.start()
	_scheduler_started = True

	print("=" * 80)
	print("POFIT Scheduler Started")
	print("India Top Picks : 08:00 IST")
	print("USA Top Picks   : 09:30 IST")
	print("=" * 80)

	return scheduler


def shutdown_scheduler():
	global _scheduler_started
	if scheduler.running:
		scheduler.shutdown(wait=False)
	_scheduler_started = False