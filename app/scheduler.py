from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.daily_top_picks_service import DailyTopPicksService
from app.repositories.stock_repository import StockRepository
from app.services.market_data_service import MarketDataService
from app.services.alpha_portfolio_service import AlphaPortfolioService
from app.services.performance_service import PerformanceService


scheduler = BackgroundScheduler(
    timezone=ZoneInfo("Asia/Kolkata")
)


def generate_india():
    print("=" * 80)
    print("POFIT Scheduler")
    print("Generating India Top Picks...")
    print("=" * 80)
    # generate daily top picks (existing behavior)
    service = DailyTopPicksService()
    result = service.generate_country("IN")
    print(f"Generated {len(result)} India Top Picks")

    # append Alpha Portfolio update and performance calculation
    alpha_service = AlphaPortfolioService()
    perf_service = PerformanceService()

    try:
        count = alpha_service.reconcile_market("IN")
        print(f"Alpha portfolio updated; holdings={count} (IN)")
    except Exception as e:
        print(f"Alpha portfolio update failed: {e}")

    try:
        snapshot = perf_service.snapshot_market("IN")
        print(f"Performance snapshot taken for IN: {snapshot}")
    except Exception as e:
        print(f"Performance snapshot failed: {e}")


def generate_usa():
    print("=" * 80)
    print("POFIT Scheduler")
    print("Generating USA Top Picks...")
    print("=" * 80)
    # generate daily top picks (existing behavior)
    service = DailyTopPicksService()
    result = service.generate_country("US")
    print(f"Generated {len(result)} USA Top Picks")

    # append Alpha Portfolio update and performance calculation
    alpha_service = AlphaPortfolioService()
    perf_service = PerformanceService()

    try:
        count = alpha_service.reconcile_market("US")
        print(f"Alpha portfolio updated; holdings={count} (US)")
    except Exception as e:
        print(f"Alpha portfolio update failed: {e}")

    try:
        snapshot = perf_service.snapshot_market("US")
        print(f"Performance snapshot taken for US: {snapshot}")
    except Exception as e:
        print(f"Performance snapshot failed: {e}")


def start_scheduler():

    scheduler.add_job(
        generate_india,
        trigger="cron",
        hour=8,
        minute=0,
        id="india_top_picks",
        replace_existing=True,
    )

    scheduler.add_job(
        generate_usa,
        trigger="cron",
        hour=9,
        minute=30,
        id="usa_top_picks",
        replace_existing=True,
    )

    scheduler.start()

    print("=" * 80)
    print("POFIT Scheduler Started")
    print("India  : 08:00 IST")
    print("USA    : 09:30 IST")
    print("=" * 80)