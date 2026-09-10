import logging
from typing import Any, Dict, List

from app.repositories.stock_repository import StockRepository
from app.repositories.daily_top_picks_repository import (
    DailyTopPicksRepository,
)
from app.services.market_data_service import MarketDataService
from app.services.alpha_filter_service import AlphaFilterService
from app.services.yahoo_service import (
    MarketDataUnavailableError,
    SymbolNotFoundError,
)


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
        top_n: int = 15,
    ) -> List[Dict[str, Any]]:

        universe = self.stock_repo.list_all_by_country(country)

        print("=" * 80)
        print(f"{country}: Found {len(universe)} stocks")
        print("=" * 80)

        ranked: List[Dict[str, Any]] = []

        for stock in universe:

            symbol = stock["symbol"]

            try:
                payload = self.market.get_stock_for_top_picks(symbol)

                print(f"{symbol} -> payload keys: {list(payload.keys())}")

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
                    print(f"{symbol} -> NOT ELIGIBLE: {reason}")
                    continue

                score = payload.get("score_json")

                print(f"{symbol} -> score = {score}")

                if not score:
                    print(f"{symbol} -> NO SCORE")
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
        print(f"{country}: Ranked {len(ranked)}")
        print("=" * 80)

        ranked.sort(
            key=lambda x: x["overall_score"],
            reverse=True,
        )

        ranked = ranked[:top_n]

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
        limit: int = 15,
    ) -> List[Dict[str, Any]]:

        return self.repository.get_country(
            country=country,
            limit=limit,
        )
