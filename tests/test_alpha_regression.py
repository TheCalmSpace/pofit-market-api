"""Regression tests for the Alpha portfolio pipeline.

These cover the regression that stopped new stocks entering the Alpha
portfolio for both markets at the same time: the `alpha_portfolio` INSERT
payload carried `score` and `rank`, but production `public.alpha_portfolio`
has neither column, so every ADD failed with SQLSTATE 42703 and aborted the
whole reconcile before any holding could be added or removed.

The schema contract is asserted against a fixture that mirrors the real
production columns, so the payload cannot silently drift again.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.alpha_portfolio_repository import AlphaPortfolioRepository
from app.services.alpha_portfolio_service import AlphaPortfolioService


# Verbatim from information_schema on the production Supabase project.
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


def build_service(current_rows, picks, prices=None):
    service = AlphaPortfolioService.__new__(AlphaPortfolioService)
    service.alpha_repo = MagicMock()
    service.history_repo = MagicMock()
    service.history_repo.ACTION_ADD = "ADD"
    service.history_repo.ACTION_REMOVE = "REMOVE"
    service.daily_repo = MagicMock()
    service.stock_data_repo = MagicMock()
    service.market = MagicMock()
    service.logger = MagicMock()

    service.alpha_repo.get_all.return_value = current_rows
    service.daily_repo.get_country.return_value = picks
    service.alpha_repo.insert_one.return_value = {"id": "1"}

    prices = prices or {}

    def get_quote_data(symbol):
        value = prices.get(symbol)
        if value is None:
            return None
        return {"quote_json": {"current_price": value}}

    service.stock_data_repo.get_quote_data.side_effect = get_quote_data
    return service


def pick(symbol, score, rank, country="IN", exchange="NSE"):
    return {
        "country": country,
        "symbol": symbol,
        "company_name": f"{symbol} LIMITED",
        "exchange": exchange,
        "overall_score": score,
        "growth_score": 10.0,
        "quality_score": 20.0,
        "financial_strength_score": 30.0,
        "valuation_score": score - 60.0,
        "rank": rank,
    }


def holding(symbol, market="IN", entry_price=100.0):
    return {
        "market": market,
        "symbol": symbol,
        "company_name": f"{symbol} LIMITED",
        "exchange": "NSE",
        "overall_score": 90.0,
        "entry_price": entry_price,
        "entry_date": "2026-01-01T00:00:00+00:00",
    }


# --------------------------------------------------------------------------
# 1. Schema contract
# --------------------------------------------------------------------------


class TestAlphaPortfolioSchemaContract:
    def test_insert_payload_only_uses_production_columns(self):
        """Every key written to alpha_portfolio must exist in production."""

        service = build_service(
            current_rows=[],
            picks=[pick("NEWCO", 95.0, 1)],
            prices={"NEWCO": 250.0},
        )

        service.reconcile_market("IN")

        service.alpha_repo.insert_one.assert_called_once()
        payload = service.alpha_repo.insert_one.call_args.args[0]

        unknown = set(payload) - PRODUCTION_ALPHA_PORTFOLIO_COLUMNS
        assert unknown == set(), (
            "alpha_portfolio INSERT payload sends columns that production does "
            f"not have: {sorted(unknown)}. This is what caused SQLSTATE 42703."
        )

    def test_payload_never_sends_score_or_rank(self):
        """The two columns that caused the outage, asserted explicitly."""

        service = build_service(
            current_rows=[],
            picks=[pick("NEWCO", 95.0, 1)],
            prices={"NEWCO": 250.0},
        )

        service.reconcile_market("IN")

        payload = service.alpha_repo.insert_one.call_args.args[0]
        assert "score" not in payload
        assert "rank" not in payload
        assert payload["overall_score"] == 95.0

    def test_score_breakdown_is_persisted(self):
        service = build_service(
            current_rows=[],
            picks=[pick("NEWCO", 95.0, 1)],
            prices={"NEWCO": 250.0},
        )

        service.reconcile_market("IN")
        payload = service.alpha_repo.insert_one.call_args.args[0]

        assert payload["growth_score"] == 10.0
        assert payload["quality_score"] == 20.0
        assert payload["financial_strength_score"] == 30.0

    def test_remove_event_uses_overall_score_column(self):
        """REMOVE reads the score back off the stored holding."""

        service = build_service(
            current_rows=[holding("OLDCO", entry_price=100.0)],
            picks=[],
        )

        service.reconcile_market("IN")

        service.alpha_repo.delete.assert_called_once()
        event = service.history_repo.insert_event.call_args.kwargs
        assert event["action"] == "REMOVE"
        assert event["score"] == 90.0


# --------------------------------------------------------------------------
# 2. The actual regression: "available after 20 September"
# --------------------------------------------------------------------------


class TestNewStockAfterSeptember20EntersAlpha:
    """A stock that only becomes eligible after 20 September must flow
    through the entire pipeline into `alpha_portfolio`, for both markets."""

    @pytest.mark.parametrize(
        "market,exchange,new_symbol",
        [
            ("IN", "NSE", "WAAREEENER"),
            ("US", "NASDAQ", "NEWUSCO"),
        ],
    )
    def test_new_stock_is_inserted(self, market, exchange, new_symbol):
        incumbent = "JAYKAY" if market == "IN" else "NVDA"
        incumbent_pick = pick(incumbent, 96.0, 1, country=market, exchange=exchange)

        service = build_service(
            current_rows=[holding(incumbent, market=market)],
            picks=[
                incumbent_pick,
                pick(new_symbol, 94.0, 2, country=market, exchange=exchange),
            ],
            prices={new_symbol: 500.0},
        )

        size = service.reconcile_market(market)

        inserted = {
            call.args[0]["symbol"]
            for call in service.alpha_repo.insert_one.call_args_list
        }
        assert inserted == {new_symbol}
        assert size == 2

    @pytest.mark.parametrize(
        "market,exchange,new_symbol",
        [
            ("IN", "NSE", "WAAREEENER"),
            ("US", "NASDAQ", "NEWUSCO"),
        ],
    )
    def test_add_event_is_written_to_history(self, market, exchange, new_symbol):
        service = build_service(
            current_rows=[],
            picks=[pick(new_symbol, 94.0, 1, country=market, exchange=exchange)],
            prices={new_symbol: 500.0},
        )

        service.reconcile_market(market)

        event = service.history_repo.insert_event.call_args.kwargs
        assert event["action"] == "ADD"
        assert event["symbol"] == new_symbol
        assert event["market"] == market
        assert event["price"] == 500.0


# --------------------------------------------------------------------------
# 3. One bad symbol must not abort the batch
# --------------------------------------------------------------------------


class TestBatchResilience:
    def test_insert_failure_does_not_block_other_adds(self):
        service = build_service(
            current_rows=[],
            picks=[
                pick("FAILCO", 99.0, 1),
                pick("GOODCO", 90.0, 2),
            ],
            prices={"FAILCO": 10.0, "GOODCO": 20.0},
        )

        calls = {"n": 0}

        def flaky_insert(row):
            calls["n"] += 1
            if row["symbol"] == "FAILCO":
                raise RuntimeError(
                    "column alpha_portfolio.score does not exist (SQLSTATE 42703)"
                )
            return {"id": "generated"}

        service.alpha_repo.insert_one.side_effect = flaky_insert

        size = service.reconcile_market("IN")

        inserted = {
            call.args[0]["symbol"]
            for call in service.alpha_repo.insert_one.call_args_list
        }
        assert calls["n"] == 2
        assert inserted == {"FAILCO", "GOODCO"}
        assert size == 2
        service.logger.error.assert_called()

    def test_insert_failure_does_not_block_removals(self):
        service = build_service(
            current_rows=[holding("OLDCO")],
            picks=[pick("NEWCO", 90.0, 1)],
            prices={"NEWCO": 20.0},
        )
        service.alpha_repo.insert_one.side_effect = RuntimeError("boom")

        service.reconcile_market("IN")

        service.alpha_repo.delete.assert_called_once_with("OLDCO", market="IN")

    def test_insert_returning_none_does_not_block_removals(self):
        service = build_service(
            current_rows=[holding("OLDCO")],
            picks=[pick("NEWCO", 90.0, 1)],
            prices={"NEWCO": 20.0},
        )
        service.alpha_repo.insert_one.return_value = None

        service.reconcile_market("IN")

        service.alpha_repo.delete.assert_called_once_with("OLDCO", market="IN")

    def test_delete_failure_does_not_stop_the_remaining_removals(self):
        service = build_service(
            current_rows=[holding("OLDONE"), holding("OLDTWO")],
            picks=[],
        )
        calls = {"n": 0}

        def flaky_delete(symbol, market=None):
            calls["n"] += 1
            if symbol == "OLDONE":
                raise RuntimeError("transient")

        service.alpha_repo.delete.side_effect = flaky_delete

        service.reconcile_market("IN")
        assert calls["n"] == 2


# --------------------------------------------------------------------------
# 4. Entry price handling
# --------------------------------------------------------------------------


class TestEntryPrice:
    def test_existing_entry_price_is_preserved(self):
        service = build_service(
            current_rows=[holding("JAYKAY", entry_price=165.32)],
            picks=[pick("JAYKAY", 96.0, 1)],
            prices={"JAYKAY": 999.0},
        )

        service.reconcile_market("IN")

        # Nothing to add, so nothing is written.
        service.alpha_repo.insert_one.assert_not_called()
        service.stock_data_repo.get_quote_data.assert_not_called()

    def test_new_symbol_falls_back_to_cached_quote(self):
        service = build_service(
            current_rows=[],
            picks=[pick("NEWCO", 90.0, 1)],
            prices={"NEWCO": 123.45},
        )

        service.reconcile_market("IN")
        payload = service.alpha_repo.insert_one.call_args.args[0]

        assert payload["entry_price"] == 123.45
        service.market.get_stock.assert_not_called()

    def test_symbol_without_any_price_is_skipped_not_fatal(self):
        service = build_service(
            current_rows=[],
            picks=[pick("NOPRICE", 90.0, 1)],
            prices={},
        )
        service.market.get_stock.side_effect = RuntimeError("no quote")

        size = service.reconcile_market("IN")

        service.alpha_repo.insert_one.assert_not_called()
        assert size == 0


# --------------------------------------------------------------------------
# 5. Idempotency / duplicate prevention
# --------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_with_unchanged_picks_writes_nothing(self):
        picks = [pick("JAYKAY", 96.0, 1), pick("BLS", 95.0, 2)]
        current = [
            holding("JAYKAY", entry_price=165.32),
            holding("BLS", entry_price=231.58),
        ]

        service = build_service(current_rows=current, picks=picks)
        service.reconcile_market("IN")

        service.alpha_repo.insert_one.assert_not_called()
        service.alpha_repo.delete.assert_not_called()

    def test_no_duplicate_rows_when_a_symbol_is_added_twice_in_the_picks(self):
        service = build_service(
            current_rows=[],
            picks=[pick("DUPCO", 90.0, 1), pick("DUPCO", 89.0, 2)],
            prices={"DUPCO": 10.0},
        )

        size = service.reconcile_market("IN")

        assert service.alpha_repo.insert_one.call_count == 1
        assert size == 1


# --------------------------------------------------------------------------
# 6. Repository contract used by the service
# --------------------------------------------------------------------------


class TestRepositoryContract:
    def test_insert_one_exists(self):
        """`reconcile_market` calls `insert_one`; removing it silently broke
        every ADD with an AttributeError."""
        assert hasattr(AlphaPortfolioRepository, "insert_one")
        assert callable(AlphaPortfolioRepository.insert_one)

    def test_get_all_returns_entry_price(self):
        """`reconcile_market` reads `entry_price` off the rows returned by
        `get_all`, so the column must be part of the projection. `*` is
        acceptable because it includes it."""

        captured = {}

        class FakeQuery:
            def select(self, columns):
                captured["columns"] = columns
                return self

            def eq(self, *args, **kwargs):
                return self

            def execute(self):
                return MagicMock(data=[])

        with patch(
            "app.repositories.alpha_portfolio_repository.supabase.table",
            return_value=FakeQuery(),
        ):
            AlphaPortfolioRepository().get_all("IN")

        columns = captured["columns"]
        assert columns == "*" or "entry_price" in columns
