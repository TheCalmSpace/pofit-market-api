import logging
from datetime import datetime, timezone
from typing import Dict, List

from app.repositories.daily_top_picks_repository import DailyTopPicksRepository
from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.repositories.alpha_history_repository import AlphaHistoryRepository
from app.repositories.stock_data_repository import StockDataRepository
from app.services.market_data_service import MarketDataService


def _stable_unique(symbols) -> List[str]:
    """De-duplicate while preserving order.

    A symbol that appears more than once in `daily_top_picks` would otherwise
    be INSERTed (or DELETEd) more than once in the same reconcile.
    """

    seen = set()
    result: List[str] = []
    for symbol in symbols:
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result


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

    ALPHA_SIZE = 15

    def __init__(self):
        self.daily_repo = DailyTopPicksRepository()
        self.alpha_repo = AlphaPortfolioRepository()
        self.history_repo = AlphaHistoryRepository()
        self.market = MarketDataService()
        self.stock_data_repo = StockDataRepository()
        self.logger = logging.getLogger(__name__)

    def _get_current_price(self, symbol: str) -> float:
        cached = self.stock_data_repo.get_quote_data(symbol)
        if cached:
            quote = cached.get("quote_json") or {}
            current_price = quote.get("current_price")
            if current_price is not None and float(current_price) > 0:
                return float(current_price)

        payload = self.market.get_stock(symbol)
        quote = payload.get("quote_json") or {}
        current_price = quote.get("current_price")
        if current_price is None or float(current_price) <= 0:
            raise ValueError(f"No valid current price for {symbol}")
        return float(current_price)

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

        picks = self.daily_repo.get_country(country=market, limit=self.ALPHA_SIZE)
        picks_symbols = [r.get("symbol", "").upper() for r in picks]

        to_add = _stable_unique(s for s in picks_symbols if s not in current_symbols)
        to_remove = _stable_unique(s for s in current_symbols if s not in picks_symbols)

        current_by_symbol = {
            (row.get("symbol") or "").upper(): row for row in current_rows
        }
        picks_by_symbol = {
            (pick.get("symbol") or "").upper(): pick for pick in picks
        }
        timestamp = datetime.now(timezone.utc).isoformat()
        new_rows: Dict[str, dict] = {}

        for pick in picks:
            symbol = (pick.get("symbol") or "").upper()
            existing = current_by_symbol.get(symbol)
            entry_price = existing.get("entry_price") if existing else None
            entry_date = existing.get("entry_date") if existing else None

            try:
                if entry_price is None or float(entry_price) <= 0:
                    raise ValueError("entry price is missing or non-positive")
                entry_price = float(entry_price)
            except (TypeError, ValueError):
                try:
                    entry_price = self._get_current_price(symbol)
                except Exception as exc:
                    self.logger.warning(
                        "Skipping Alpha holding %s without a valid entry price: %s",
                        symbol,
                        exc,
                    )
                    continue

            if entry_date is None:
                entry_date = timestamp

            # The keys below MUST match the real `public.alpha_portfolio`
            # columns. Production stores the score breakdown in
            # `overall_score`/`growth_score`/`quality_score`/
            # `financial_strength_score`/`valuation_score`; it has no `score`
            # and no `rank` column. Sending `score`/`rank` made every ADD fail
            # with SQLSTATE 42703 ("column alpha_portfolio.score does not
            # exist"), which aborted the whole reconcile before any holding
            # could be added or removed, for both markets at once.
            new_rows[symbol] = {
                "market": market,
                "symbol": symbol,
                "company_name": pick.get("company_name"),
                "exchange": pick.get("exchange"),
                "overall_score": pick.get("overall_score"),
                "growth_score": pick.get("growth_score"),
                "quality_score": pick.get("quality_score"),
                "financial_strength_score": pick.get("financial_strength_score"),
                "valuation_score": pick.get("valuation_score"),
                "entry_price": entry_price,
                "entry_date": entry_date,
            }

        for symbol in to_add:
            if symbol not in new_rows:
                continue
            row = new_rows[symbol]

            # A single holding must never be able to abort the whole
            # reconcile. Previously any INSERT failure raised out of this loop,
            # which skipped every remaining ADD and also every REMOVE, leaving
            # the portfolio frozen at its previous contents while the scheduler
            # logged a single line and carried on.
            try:
                inserted = self.alpha_repo.insert_one(row)
            except Exception as exc:
                self.logger.error(
                    "Failed to add Alpha holding %s (%s); continuing with the "
                    "rest of the batch: %s",
                    symbol,
                    market,
                    exc,
                )
                continue

            if inserted is None:
                self.logger.error(
                    "Alpha holding %s (%s) was not written; continuing with the "
                    "rest of the batch",
                    symbol,
                    market,
                )
                continue

            pick = picks_by_symbol[symbol]
            self.history_repo.insert_event(
                market=market,
                symbol=symbol,
                action=self.history_repo.ACTION_ADD,
                reason="Added from Daily Top Picks",
                price=row["entry_price"],
                score=pick.get("overall_score"),
                company_name=pick.get("company_name"),
            )

        for symbol in to_remove:
            row = current_by_symbol[symbol]
            try:
                self.alpha_repo.delete(symbol, market=market)
            except Exception as exc:
                self.logger.error(
                    "Failed to remove Alpha holding %s (%s); continuing: %s",
                    symbol,
                    market,
                    exc,
                )
                continue

            self.history_repo.insert_event(
                market=market,
                symbol=symbol,
                action=self.history_repo.ACTION_REMOVE,
                reason="Removed from Daily Top Picks",
                price=None,
                score=row.get("overall_score"),
                company_name=row.get("company_name"),
            )

        return len(new_rows)
