import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.daily_top_picks_service import DailyTopPicksService
from app.services.alpha_portfolio_service import AlphaPortfolioService
from app.services.market_data_service import MarketDataService
from app.services.performance_service import PerformanceService
from app.services.stock_data_ingestion_service import StockDataIngestionService
from app.services.universe_sync_service import UniverseSyncService
from app.utils.symbol import MARKET_BENCHMARKS


scheduler = BackgroundScheduler(
	timezone=ZoneInfo("Asia/Kolkata")
)
_scheduler = scheduler
_scheduler_started = False
logger = logging.getLogger(__name__)

INGESTION_HOUR = int(os.getenv("STOCK_DATA_INGESTION_HOUR", "6"))
INGESTION_MINUTE = int(os.getenv("STOCK_DATA_INGESTION_MINUTE", "0"))
INGESTION_INTERVAL_HOURS = int(os.getenv("STOCK_DATA_INGESTION_INTERVAL_HOURS", "4"))

# The NSE universe sync discovers new listings and must finish before the
# 06:00 IST stock-data ingestion, so the intended order is
#   NSE Universe Sync -> Stock Data Ingestion -> India Top Picks -> Alpha.
# It is deliberately 05:30 rather than 06:00: the ingestion job's first run
# is pinned to 06:00 IST, so a 06:00 sync would run both concurrently instead
# of sequentially and would put the sync's NSE calls in parallel with
# ingestion's Yahoo calls.
UNIVERSE_SYNC_HOUR = int(os.getenv("NSE_UNIVERSE_SYNC_HOUR", "5"))
UNIVERSE_SYNC_MINUTE = int(os.getenv("NSE_UNIVERSE_SYNC_MINUTE", "30"))


def _refresh_benchmark_quote(market: str):
	"""Refresh the cached quote for `market`'s benchmark index.

	Runs once per market, immediately before that market's performance
	snapshot. Placing it here means the benchmark and the alpha holdings are
	both priced from the same trading session, so the resulting alpha is a
	like-for-like comparison rather than two windows a session apart.

	Cost: exactly one quote call per market per day (measured at roughly
	30 KB for ^NSEI and 34 KB for ^IXIC). No new scheduler and no new job is
	registered - this rides the Top Picks jobs that already run. No historical
	download is requested, and the benchmark is never added to the `stocks`
	universe, so the bounded ingestion job cannot pick it up and start
	refreshing it every 4 hours.

	A failure here must not abort the market run: the snapshot still has to be
	taken, and PerformanceService carries the benchmark NAV forward unchanged
	and reports it as missing, which is the pre-existing behaviour.
	"""
	benchmark_symbol = MARKET_BENCHMARKS.get((market or "").upper())

	if not benchmark_symbol:
		return

	try:
		MarketDataService().refresh_benchmark_quote(benchmark_symbol)
		print(f"Benchmark quote refreshed: {benchmark_symbol} ({market})")
	except Exception:
		logger.exception(
			"Benchmark quote refresh failed for %s (%s)",
			benchmark_symbol,
			market,
		)


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

	_refresh_benchmark_quote("IN")

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

	_refresh_benchmark_quote("US")

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


def sync_nse_universe():
	print("=" * 80)
	print("POFIT Scheduler")
	print("Running NSE universe sync...")
	print("=" * 80)
	try:
		report = UniverseSyncService().run()
		print(f"Universe sync complete: {report.summary()}")
		if report.final_status == "FAILED":
			logger.error("NSE universe sync finished with status FAILED: %s", report.summary())
	except Exception as exc:
		# A failed sync must never propagate: APScheduler would log it as a
		# job error, and the ingestion and Top Picks jobs scheduled after it
		# must still run on their normal cadence.
		logger.exception("NSE universe sync failed: %s", exc)


def start_scheduler():
	global _scheduler_started

	if scheduler.running:
		return scheduler

	scheduler.add_job(
		sync_nse_universe,
		trigger="cron",
		hour=UNIVERSE_SYNC_HOUR,
		minute=UNIVERSE_SYNC_MINUTE,
		id="nse_universe_sync",
		replace_existing=True,
		coalesce=True,
		max_instances=1,
	)

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
	print(f"NSE Universe Sync      : {UNIVERSE_SYNC_HOUR:02d}:{UNIVERSE_SYNC_MINUTE:02d} IST")
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