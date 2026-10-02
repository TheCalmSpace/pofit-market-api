import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.daily_top_picks_service import DailyTopPicksService
from app.services.alpha_portfolio_service import AlphaPortfolioService
from app.services.performance_service import PerformanceService
from app.services.stock_data_ingestion_service import StockDataIngestionService


scheduler = BackgroundScheduler(
	timezone=ZoneInfo("Asia/Kolkata")
)
_scheduler = scheduler
_scheduler_started = False
logger = logging.getLogger(__name__)

INGESTION_HOUR = int(os.getenv("STOCK_DATA_INGESTION_HOUR", "6"))
INGESTION_MINUTE = int(os.getenv("STOCK_DATA_INGESTION_MINUTE", "0"))
INGESTION_INTERVAL_HOURS = int(os.getenv("STOCK_DATA_INGESTION_INTERVAL_HOURS", "4"))


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


def ingest_stock_data():
	print("=" * 80)
	print("POFIT Scheduler")
	print("Running stock_data ingestion...")
	print("=" * 80)
	service = StockDataIngestionService()
	try:
		report = service.run()
		print(f"Ingestion complete: {report.summary()}")
	except Exception as exc:
		logger.exception("Stock data ingestion failed: %s", exc)


def start_scheduler():
	global _scheduler_started

	if scheduler.running:
		return scheduler

	scheduler.add_job(
		ingest_stock_data,
		trigger="interval",
		hours=INGESTION_INTERVAL_HOURS,
		id="stock_data_ingestion",
		replace_existing=True,
		coalesce=True,
		max_instances=1,
		next_run_time=datetime.now(ZoneInfo("Asia/Kolkata")).replace(
			hour=INGESTION_HOUR, minute=INGESTION_MINUTE, second=0, microsecond=0
		),
	)

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
	print(f"Stock Data Ingestion: every {INGESTION_INTERVAL_HOURS}h starting {INGESTION_HOUR:02d}:{INGESTION_MINUTE:02d} IST")
	print("India Top Picks       : 08:00 IST")
	print("USA Top Picks         : 09:30 IST")
	print("=" * 80)

	return scheduler


def shutdown_scheduler():
	global _scheduler_started
	if scheduler.running:
		scheduler.shutdown(wait=False)
	_scheduler_started = False