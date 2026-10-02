"""End-to-end regression test for the Alpha pipeline.

This exercises the real write path:

    new stock -> daily_top_picks -> AlphaPortfolioService.reconcile_market()
              -> AlphaPortfolioRepository -> alpha_portfolio

against an in-memory PostgREST double that enforces the real production
column set. The double raises SQLSTATE 42703 for any column production does
not have, exactly as PostgREST does, so the historical `score`/`rank`
payload cannot pass silently.

Both markets are covered, because the production outage hit India and the
USA at the same moment through this single shared code path.
"""

from unittest.mock import patch

import pytest

from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.services.alpha_portfolio_service import AlphaPortfolioService


PRODUCTION_ALPHA_PORTFOLIO_COLUMNS = {
    "id",
    "market",
    "symbol",
    "company_name",
    "exchange",
    "entry_date",
    "entry_price",
    "current_price",
    "overall_score",
    "growth_score",
    "quality_score",
    "financial_strength_score",
    "valuation_score",
    "status",
    "created_at",
    "updated_at",
}


class PGRST205(Exception):
    """Column/table missing, as PostgREST reports it."""


class InMemoryPostgrest:
    """Minimal PostgREST double that only speaks what these tests need."""

    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self._filters = []
        self._payload = None
        self._op = "select"

    # -- chainable -------------------------------------------------------
    def select(self, columns):
        self._op = "select"
        self._columns = columns
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def upsert(self, payload):
        self._op = "upsert"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def limit(self, _n):
        return self

    def order(self, *_a, **_k):
        return self

    # -- execute ---------------------------------------------------------
    def execute(self):
        rows = self.store.setdefault(self.table_name, [])

        if self._op in ("insert", "upsert"):
            for row in self._payload if isinstance(self._payload, list) else [self._payload]:
                unknown = set(row) - PRODUCTION_ALPHA_PORTFOLIO_COLUMNS
                if unknown:
                    # Reproduce the production failure mode exactly.
                    raise PGRST205(
                        f"column alpha_portfolio.{sorted(unknown)[0]} does not exist"
                    )
                rows.append(dict(row))
            return type("R", (), {"data": [dict(self._payload)], "count": len(rows)})()

        if self._op == "delete":
            keep = [
                r
                for r in rows
                if not all(r.get(c) == v for c, v in self._filters)
            ]
            self.store[self.table_name] = keep
            return type("R", (), {"data": [], "count": len(keep)})()

        matched = [
            r for r in rows if all(r.get(c) == v for c, v in self._filters)
        ]
        return type("R", (), {"data": matched, "count": len(matched)})()


def build_store(market, holdings, picks):
    """Seed the double with existing holdings and today's top picks."""

    return {
        "alpha_portfolio": [dict(h) for h in holdings],
        "daily_top_picks": [dict(p) for p in picks],
        "alpha_history": [],
    }


def holding(symbol, market, price, day="2026-09-20"):
    return {
        "market": market,
        "symbol": symbol,
        "company_name": f"{symbol} LIMITED",
        "exchange": "NSE" if market == "IN" else "NASDAQ",
        "entry_date": f"{day}T03:00:00+00:00",
        "entry_price": price,
        "overall_score": 90.0,
        "status": "ACTIVE",
    }


def pick(symbol, market, score, rank):
    return {
        "country": market,
        "symbol": symbol,
        "company_name": f"{symbol} LIMITED",
        "exchange": "NSE" if market == "IN" else "NASDAQ",
        "overall_score": score,
        "growth_score": 10.0,
        "quality_score": 20.0,
        "financial_strength_score": 30.0,
        "valuation_score": score - 60.0,
        "rank": rank,
    }


def run_reconcile(market, holdings, picks, prices):
    store = build_store(market, holdings, picks)

    def table(name):
        return InMemoryPostgrest(name, store)

    with patch(
        "app.repositories.alpha_portfolio_repository.supabase.table", side_effect=table
    ), patch(
        "app.repositories.daily_top_picks_repository.supabase.table", side_effect=table
    ), patch(
        "app.repositories.alpha_history_repository.supabase.table", side_effect=table
    ):
        service = AlphaPortfolioService.__new__(AlphaPortfolioService)
        service.alpha_repo = AlphaPortfolioRepository()
        service.daily_repo = AlphaPortfolioRepository.__new__(AlphaPortfolioRepository)
        service.daily_repo = _FakePicksRepository(picks)
        service.history_repo = _FakeHistoryRepository(store)
        service.stock_data_repo = _FakeStockDataRepository(prices)
        service.market = _NoNetworkMarket()
        service.logger = _QuietLogger()

        size = service.reconcile_market(market)
        return store, size


class _FakePicksRepository:
    def __init__(self, picks):
        self._picks = picks

    def get_country(self, country, limit):
        return [p for p in self._picks if p["country"] == country][:limit]


class _FakeHistoryRepository:
    def __init__(self, store):
        self._store = store

    ACTION_ADD = "ADD"
    ACTION_REMOVE = "REMOVE"

    def insert_event(self, **kwargs):
        self._store["alpha_history"].append(kwargs)
        return kwargs


class _FakeStockDataRepository:
    def __init__(self, prices):
        self._prices = prices

    def get_quote_data(self, symbol):
        price = self._prices.get(symbol)
        if price is None:
            return None
        return {"quote_json": {"current_price": price}}


class _NoNetworkMarket:
    """Any attempt to reach Yahoo fails the test loudly."""

    def get_stock(self, symbol):
        raise AssertionError(
            f"reconcile_market must not call Yahoo; it hit the network for {symbol}"
        )


class _QuietLogger:
    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


# ==========================================================================
# The regression: a stock that only becomes eligible after 20 September
# ==========================================================================


class TestPostSeptember20StockReachesAlphaPortfolio:
    @pytest.mark.parametrize(
        "market,old_symbol,new_symbol,prices",
        [
            ("IN", "JAYKAY", "WAAREEENER", {"WAAREEENER": 512.40}),
            ("US", "NVDA", "NEWUSCO", {"NEWUSCO": 88.10}),
        ],
    )
    def test_new_stock_is_persisted_to_alpha_portfolio(
        self, market, old_symbol, new_symbol, prices
    ):
        holdings = [holding(old_symbol, market, 100.0)]
        picks = [
            pick(old_symbol, market, 96.0, 1),
            pick(new_symbol, market, 92.0, 2),
        ]

        store, size = run_reconcile(market, holdings, picks, prices)

        rows = store["alpha_portfolio"]
        symbols = {r["symbol"] for r in rows}

        assert new_symbol in symbols, (
            f"{new_symbol} did not reach alpha_portfolio; stored={symbols}"
        )
        assert old_symbol in symbols, "existing holding must be preserved"
        assert size == 2

    @pytest.mark.parametrize(
        "market,new_symbol,prices",
        [
            ("IN", "WAAREEENER", {"WAAREEENER": 512.40}),
            ("US", "NEWUSCO", {"NEWUSCO": 88.10}),
        ],
    )
    def test_new_stock_row_matches_production_schema(
        self, market, new_symbol, prices
    ):
        store, _ = run_reconcile(
            market, [], [pick(new_symbol, market, 92.0, 1)], prices
        )

        row = next(r for r in store["alpha_portfolio"] if r["symbol"] == new_symbol)

        # The contract the outage violated.
        assert "score" not in row
        assert "rank" not in row

        # The production column that must carry the score.
        assert row["overall_score"] == 92.0
        assert set(row) <= PRODUCTION_ALPHA_PORTFOLIO_COLUMNS

    @pytest.mark.parametrize(
        "market,new_symbol,prices",
        [
            ("IN", "WAAREEENER", {"WAAREEENER": 512.40}),
            ("US", "NEWUSCO", {"NEWUSCO": 88.10}),
        ],
    )
    def test_add_event_written_to_alpha_history(self, market, new_symbol, prices):
        store, _ = run_reconcile(
            market, [], [pick(new_symbol, market, 92.0, 1)], prices
        )

        adds = [e for e in store["alpha_history"] if e["action"] == "ADD"]

        assert len(adds) == 1
        assert adds[0]["symbol"] == new_symbol
        assert adds[0]["market"] == market
        assert adds[0]["score"] == 92.0
        assert adds[0]["price"] == prices[new_symbol]


# ==========================================================================
# Duplicate candidates
# ==========================================================================


class TestDuplicateCandidates:
    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_duplicate_candidates_produce_one_row(self, market):
        dup = "DUPCO"
        picks = [
            pick(dup, market, 95.0, 1),
            pick(dup, market, 94.0, 2),
            pick(dup, market, 93.0, 3),
        ]

        store, size = run_reconcile(market, [], picks, {dup: 10.0})

        rows = [r for r in store["alpha_portfolio"] if r["symbol"] == dup]
        assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
        assert size == 1

    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_duplicate_removals_delete_once(self, market):
        holdings = [holding("GONE", market, 10.0)]
        picks = [pick("KEPT", market, 90.0, 1)]

        store, _ = run_reconcile(market, holdings, picks, {"KEPT": 20.0})

        removals = [e for e in store["alpha_history"] if e["action"] == "REMOVE"]
        assert len(removals) == 1
        assert removals[0]["symbol"] == "GONE"


# ==========================================================================
# Idempotent second run
# ==========================================================================


class TestIdempotency:
    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_second_identical_run_writes_nothing(self, market):
        symbol = "STEADY"
        holdings = [holding(symbol, market, 165.32)]
        picks = [pick(symbol, market, 96.0, 1)]
        prices = {symbol: 999.0}

        store, _ = run_reconcile(market, holdings, picks, prices)
        first = len(store["alpha_history"])

        # Re-run against the state the first run produced.
        store2, _ = run_reconcile(
            market, store["alpha_portfolio"], picks, prices
        )

        assert len(store2["alpha_portfolio"]) == 1
        assert len(store2["alpha_history"]) == first, (
            "a no-change reconcile must not emit ADD/REMOVE events"
        )

    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_entry_price_survives_a_second_run(self, market):
        symbol = "STEADY"
        holdings = [holding(symbol, market, 165.32)]
        picks = [pick(symbol, market, 96.0, 1)]

        store, _ = run_reconcile(market, holdings, picks, {})

        assert store["alpha_portfolio"][0]["entry_price"] == 165.32


# ==========================================================================
# entry_price / overall_score handling
# ==========================================================================


class TestEntryPriceAndScore:
    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_existing_entry_price_is_not_repriced(self, market):
        symbol = "STEADY"
        store, _ = run_reconcile(
            market,
            [holding(symbol, market, 165.32)],
            [pick(symbol, market, 96.0, 1)],
            {symbol: 9999.0},
        )
        assert store["alpha_portfolio"][0]["entry_price"] == 165.32

    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_new_symbol_entry_price_comes_from_cache(self, market):
        symbol = "FRESHCO"
        store, _ = run_reconcile(
            market, [], [pick(symbol, market, 90.0, 1)], {symbol: 321.5}
        )
        row = store["alpha_portfolio"][0]
        assert row["entry_price"] == 321.5
        assert row["entry_date"] is not None

    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_new_symbol_with_no_price_is_skipped_not_fatal(self, market):
        symbol = "NOPRICE"
        incumbent = "STEADY"

        store, size = run_reconcile(
            market,
            [holding(incumbent, market, 50.0)],
            [pick(incumbent, market, 96.0, 1), pick(symbol, market, 80.0, 2)],
            {},
        )

        symbols = {r["symbol"] for r in store["alpha_portfolio"]}
        assert symbols == {incumbent}
        assert size == 1

    @pytest.mark.parametrize("market", ["IN", "US"])
    def test_overall_score_breakdown_is_persisted(self, market):
        symbol = "SCORED"
        store, _ = run_reconcile(
            market, [], [pick(symbol, market, 88.5, 1)], {symbol: 10.0}
        )

        row = store["alpha_portfolio"][0]
        assert row["overall_score"] == 88.5
        assert row["growth_score"] == 10.0
        assert row["quality_score"] == 20.0
        assert row["financial_strength_score"] == 30.0
        assert row["valuation_score"] == 28.5
