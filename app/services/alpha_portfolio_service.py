from datetime import datetime, timezone
from typing import Dict, List

from app.repositories.daily_top_picks_repository import DailyTopPicksRepository
from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.repositories.alpha_history_repository import AlphaHistoryRepository


class AlphaPortfolioService:
    """Service that reconciles the Alpha Portfolio with today's
    `Daily Top Picks`.

    This service performs these steps for a single market:
    1. Read the current `alpha_portfolio`.
    2. Read today's `daily_top_picks` (top 15).
    3. Compare both lists and determine which symbols are `ADD` or
       `REMOVE`. Existing symbols that remain are implicitly `KEEP`.
    4. Write `ADD` and `REMOVE` events to `alpha_history`.
    5. Replace the `alpha_portfolio` table contents for the market
       with the new top-15 rows.

    Important: this class contains comparison logic only. It does not
    implement ranking, thresholds, partial rebalancing, or any
    scoring/eligibility rules.
    """

    def __init__(self):
        self.daily_repo = DailyTopPicksRepository()
        self.alpha_repo = AlphaPortfolioRepository()
        self.history_repo = AlphaHistoryRepository()

    def reconcile_all(self) -> Dict[str, int]:
        """Reconcile both supported markets and return resulting sizes.

        Returns a dict with keys `India` and `USA` mapping to the
        resulting portfolio sizes.
        """

        return {
            "India": self.reconcile_market("IN"),
            "USA": self.reconcile_market("US"),
        }

    def reconcile_market(self, market: str) -> int:
        """Reconcile a single market's alpha portfolio.

        Steps:
        - Load current portfolio and today's top picks.
        - Compute symbols to add and remove.
        - Record `ADD` and `REMOVE` events in `alpha_history`.
        - Replace the `alpha_portfolio` rows for the market with the
          new top-15 rows derived from the daily picks.

        Returns the resulting portfolio size (should be 15).
        """

        market = (market or "").upper()

        current_rows = self.alpha_repo.get_all(market)
        current_symbols = [r.get("symbol", "").upper() for r in current_rows]

        picks = self.daily_repo.get_country(country=market, limit=15)
        picks_symbols = [r.get("symbol", "").upper() for r in picks]

        to_add = [s for s in picks_symbols if s not in current_symbols]
        to_remove = [s for s in current_symbols if s not in picks_symbols]

        # Record REMOVE events
        for symbol in to_remove:
            # find metadata from current portfolio
            row = next((r for r in current_rows if (r.get("symbol") or "").upper() == symbol), None)

            company_name = row.get("company_name") if row else None
            score = row.get("score") if row else None

            self.history_repo.insert_event(
                market=market,
                symbol=symbol,
                action=self.history_repo.ACTION_REMOVE,
                reason="Removed from Daily Top Picks",
                price=None,
                score=score,
                company_name=company_name,
            )

        # Record ADD events
        for symbol in to_add:
            pick = next((r for r in picks if (r.get("symbol") or "").upper() == symbol), None)

            company_name = pick.get("company_name") if pick else None
            score = pick.get("overall_score") if pick else None

            self.history_repo.insert_event(
                market=market,
                symbol=symbol,
                action=self.history_repo.ACTION_ADD,
                reason="Added from Daily Top Picks",
                price=None,
                score=score,
                company_name=company_name,
            )

        # Build new portfolio rows from picks and replace the table
        new_rows: List[dict] = []
        timestamp = datetime.now(timezone.utc).isoformat()

        for pick in picks:
            new_rows.append(
                {
                    "market": market,
                    "symbol": (pick.get("symbol") or "").upper(),
                    "company_name": pick.get("company_name"),
                    "exchange": pick.get("exchange"),
                    "score": pick.get("overall_score"),
                    "rank": pick.get("rank"),
                    "entry_date": timestamp,
                }
            )

        # Replace persisted portfolio
        self.alpha_repo.replace_all(market, new_rows)

        return len(new_rows)
