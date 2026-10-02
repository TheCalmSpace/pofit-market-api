import logging
import os
from typing import Any, Dict, List, Optional

from app.repositories.stock_repository import StockRepository
from app.repositories.daily_top_picks_repository import (
    DailyTopPicksRepository,
)
from app.repositories.stock_data_repository import StockDataRepository
from app.services.market_data_service import MarketDataService
from app.services.alpha_filter_service import AlphaFilterService
from app.services.yahoo_service import (
    MarketDataUnavailableError,
    SymbolNotFoundError,
)


# Ranking the whole universe from live Yahoo data costs roughly 258 KB per
# symbol. With 3,566 NSE and 8,456 US symbols and a 6 hour cache TTL that is
# about 3 GB of Yahoo egress per daily Top Picks cycle, because the scheduled
# ingestion job can only refresh 30 symbols every 4 hours (180 a day) and the
# rest of the universe is therefore always stale at ranking time.
#
# Scores are derived from fundamentals and 5 year price history, which move
# far more slowly than quotes, so a cached score is still a valid ranking
# input. Ranking therefore runs off the cache and live Yahoo calls are capped:
#   - symbols that have never been scored get a full refresh, so newly added
#     listings still enter the ranking;
#   - only the leading candidates get a cheap quote-only refresh, so anything
#     that can actually enter the portfolio carries a current price.
REFRESH_BUDGET = int(os.getenv("TOP_PICKS_REFRESH_BUDGET", "40"))
QUOTE_REFRESH_BUDGET = int(os.getenv("TOP_PICKS_QUOTE_REFRESH_BUDGET", "40"))


class DailyTopPicksService:
    """
    Generates the daily Top Picks list for each market.

    This service deliberately reuses MarketDataService so that all
    caching, metrics calculation and scoring continue to live in one
    place.
    """

    logger = logging.getLogger(__name__)

    def __init__(self):

        self.market = MarketDataService()
        self.alpha_filter = AlphaFilterService()

        self.stock_repo = StockRepository()
        self.stock_data_repo = StockDataRepository()

        self.repository = DailyTopPicksRepository()

    def generate_all(self) -> Dict[str, int]:

        india = self.generate_country("IN")

        usa = self.generate_country("US")

        return {
            "India": len(india),
            "USA": len(usa),
        }

    def generate_country(
        self,
        country: str,
        top_n: int = 50,
        refresh_budget: Optional[int] = None,
        quote_refresh_budget: Optional[int] = None,
    ) -> List[Dict[str, Any]]:

        budget = REFRESH_BUDGET if refresh_budget is None else refresh_budget
        quote_budget = (
            QUOTE_REFRESH_BUDGET if quote_refresh_budget is None else quote_refresh_budget
        )

        universe = self.stock_repo.list_all_by_country(country)

        print("=" * 80)
        print(f"{country}: Found {len(universe)} stocks")
        print("=" * 80)

        symbols = [stock["symbol"] for stock in universe]

        stock_meta_list = self.stock_repo.get_many_by_symbol_for_top_picks(symbols)
        stock_data_list = self.stock_data_repo.get_many_for_top_picks(symbols)

        stock_meta = {s["symbol"]: s for s in stock_meta_list}
        stock_data = {s["symbol"]: s for s in stock_data_list}

        ranked: List[Dict[str, Any]] = []
        refreshes_used = 0
        refresh_failures = 0

        for stock in universe:

            symbol = stock["symbol"]

            try:
                meta = stock_meta.get(symbol)
                if not meta:
                    self.logger.info("Skipping %s: stock metadata not found", symbol)
                    continue

                cached = stock_data.get(symbol)
                usable_cached_score = bool(
                    cached
                    and cached.get("score_json")
                    and cached.get("eligibility_json")
                )

                if cached and self.market._is_cache_valid(cached):
                    payload = cached
                elif usable_cached_score:
                    # Rank on the cached score. The underlying fundamentals and
                    # price history have not been re-downloaded, so this is the
                    # same score a refresh would have recomputed.
                    payload = cached
                else:
                    # Never scored: a refresh is the only way this symbol can
                    # ever be ranked, so it takes priority within the budget.
                    if refreshes_used >= budget:
                        self.logger.info(
                            "Skipping %s: no cached score and the Top Picks "
                            "refresh budget (%d) is exhausted",
                            symbol,
                            budget,
                        )
                        continue
                    refreshes_used += 1
                    try:
                        payload = self.market.refresh_stock(symbol, stock=meta)
                    except (MarketDataUnavailableError, SymbolNotFoundError) as exc:
                        refresh_failures += 1
                        self.logger.warning(
                            "Skipping %s: refresh failed during Top Picks: %s",
                            symbol,
                            exc,
                        )
                        continue

                eligibility = payload.get("eligibility_json")
                if not eligibility:
                    self.logger.info("Skipping %s: eligibility data unavailable", symbol)
                    continue

                if payload.get("score_json") is None:
                    self.logger.info(
                        "Skipping %s: required financial or historical financial data unavailable",
                        symbol,
                    )
                    continue

                eligible, reason = self.alpha_filter.is_eligible(
                    country=country,
                    eligibility=eligibility,
                )

                if not eligible:
                    continue

                score = payload.get("score_json")

                if not score:
                    continue

                ranked.append(
                    {
                        "country": country,
                        "symbol": symbol,
                        "company_name": stock.get("company_name"),
                        "exchange": stock.get("exchange"),
                        "overall_score": score.get("overall_score", 0),
                        "growth_score": score.get("growth_score", 0),
                        "quality_score": score.get("quality_score", 0),
                        "financial_strength_score": score.get(
                            "financial_strength_score", 0
                        ),
                        "valuation_score": score.get("valuation_score", 0),
                    }
                )

            except Exception:
                self.logger.exception("Skipping %s after an unexpected data-processing failure", symbol)
                continue

        print("=" * 80)
        print(
            f"{country}: Ranked {len(ranked)} "
            f"(full refreshes={refreshes_used}/{budget}, failures={refresh_failures})"
        )
        print("=" * 80)

        ranked.sort(
            key=lambda x: x["overall_score"],
            reverse=True,
        )

        ranked = ranked[:top_n]

        # Keep the leading candidates' quotes current so that the Alpha entry
        # price and the served portfolio do not depend on a stale cache. This is
        # a quote-only update: it does not touch metrics or the score.
        quotes_used = 0
        for stock in ranked[:quote_budget]:
            symbol = stock["symbol"]
            meta = stock_meta.get(symbol)
            if meta is None:
                continue
            try:
                self.market.refresh_quote(symbol, stock=meta)
                quotes_used += 1
            except Exception as exc:
                self.logger.warning("Quote refresh failed for %s: %s", symbol, exc)

        print(
            f"{country}: refreshed {quotes_used}/{min(quote_budget, len(ranked))} "
            "leading candidate quotes"
        )

        rows: List[Dict[str, Any]] = []

        for index, stock in enumerate(ranked, start=1):
            rows.append(
                {
                    "country": stock["country"],
                    "symbol": stock["symbol"],
                    "company_name": stock["company_name"],
                    "exchange": stock["exchange"],
                    "overall_score": stock["overall_score"],
                    "growth_score": stock["growth_score"],
                    "quality_score": stock["quality_score"],
                    "financial_strength_score": stock[
                        "financial_strength_score"
                    ],
                    "valuation_score": stock["valuation_score"],
                    "rank": index,
                }
            )

        print(f"{country}: Saving {len(rows)} rows")

        self.repository.replace_country(
            country=country,
            rows=rows,
        )

        return rows

    def get_country(
        self,
        country: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:

        return self.repository.get_country(
            country=country,
            limit=limit,
        )
