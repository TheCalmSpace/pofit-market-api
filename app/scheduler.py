import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.services.daily_top_picks_service import DailyTopPicksService
from app.services.alpha_portfolio_service import AlphaPortfolioService


scheduler = BackgroundScheduler(
    timezone=ZoneInfo("Asia/Kolkata")
)
logger = logging.getLogger(__name__)


def generate_india():
    print("=" * 80)
    print("POFIT Scheduler")
    print("Generating India Top Picks...")
    print("=" * 80)
    # generate daily top picks (existing behavior)
    service = DailyTopPicksService()
    result = service.generate_country("IN")
    print(f"Generated {len(result)} India Top Picks")

    # Reconcile Alpha from the already-generated eligible Daily Top Picks.
    # Performance calculation is intentionally not part of the Alpha pipeline.
    alpha_service = AlphaPortfolioService()

    try:
        count = alpha_service.reconcile_market("IN")
        print(f"Alpha portfolio updated; holdings={count} (IN)")
    except Exception:
        logger.exception("Alpha portfolio update failed for IN")

def generate_usa():
    print("=" * 80)
    print("POFIT Scheduler")
    print("Generating USA Top Picks...")
    print("=" * 80)
    # generate daily top picks (existing behavior)
    service = DailyTopPicksService()
    result = service.generate_country("US")
    print(f"Generated {len(result)} USA Top Picks")

    # Reconcile Alpha from the already-generated eligible Daily Top Picks.
    # Performance calculation is intentionally not part of the Alpha pipeline.
    alpha_service = AlphaPortfolioService()

    try:
        count = alpha_service.reconcile_market("US")
        print(f"Alpha portfolio updated; holdings={count} (US)")
    except Exception:
        logger.exception("Alpha portfolio update failed for US")

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
