"""Tests for the batched PostgREST symbol lookups used by Daily Top Picks.

Regression coverage for the production failure where a single `or=(symbol.eq
.X,...)` filter covering the whole universe (2,386 NSE symbols / 8,456 US
symbols) produced a 35KB-127KB request URL. The Supabase/Kong gateway rejects
that with a bare HTTP 400, which postgrest-py surfaces as the misleading
"JSON could not be generated" error, aborting the whole Top Picks job.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.stock_data_repository import StockDataRepository
from app.repositories.stock_repository import StockRepository
from app.utils.postgrest import (
    SYMBOL_BATCH_SIZE,
    batched,
    normalized_unique_symbols,
)


# Filter length the batching must stay comfortably below. The unsplit filter
# for the real universes is 35KB (IN) and 127KB (US).
MAX_SAFE_FILTER_LENGTH = 8_000

IN_UNIVERSE_SIZE = 2_386
US_UNIVERSE_SIZE = 8_456


def _symbols(count, prefix):
    return ["%s%05d" % (prefix, index) for index in range(count)]


def _configure_stocks_lookup(supabase_mock, rows_for_or_filter):
    """Wire `supabase.table().select().or_(...).eq().execute()` for `stocks`.

    `rows_for_or_filter` maps an `or` filter string to the rows the fake
    PostgREST response should return.
    """
    recorded = []

    def _select(columns):
        response = MagicMock()
        response.or_.side_effect = _or_
        return response

    def _or_(or_filter):
        recorded.append(or_filter)
        response = MagicMock()
        response.eq.return_value.execute.return_value = SimpleNamespace(
            data=rows_for_or_filter(or_filter)
        )
        return response

    supabase_mock.table.return_value.select.side_effect = _select
    return recorded


def _configure_stock_data_lookup(supabase_mock, rows_for_or_filter):
    """Wire `supabase.table().select().or_().execute()` for `stock_data`."""
    recorded = []

    def _select(columns):
        response = MagicMock()
        response.or_.side_effect = _or_
        return response

    def _or_(or_filter):
        recorded.append(or_filter)
        response = MagicMock()
        response.execute.return_value = SimpleNamespace(
            data=rows_for_or_filter(or_filter)
        )
        return response

    supabase_mock.table.return_value.select.side_effect = _select
    return recorded


def _rows_matching(or_filter, template):
    return [dict(template, symbol=clause.split("symbol.eq.")[1])
            for clause in or_filter.split(",")]


class TestPostgrestHelpers:
    def test_batched_covers_every_item_exactly_once(self):
        items = _symbols(2_531, "SYM")
        batches = list(batched(items, 200))

        assert [item for batch in batches for item in batch] == items
        assert all(len(batch) <= 200 for batch in batches)
        assert len(batches) == 13

    def test_india_universe_needs_twelve_requests(self):
        assert len(list(batched(_symbols(2_386, "S"), 200))) == 12

    def test_usa_universe_needs_forty_three_requests(self):
        assert len(list(batched(_symbols(8_456, "S"), 200))) == 43

    def test_batched_handles_empty_and_exact_multiples(self):
        assert list(batched([], 200)) == []
        assert len(list(batched(_symbols(400, "S"), 200))) == 2

    def test_normalized_unique_symbols_dedupes_and_uppercases(self):
        result = normalized_unique_symbols(
            ["reliance", " RELIANCE ", "tcs", "", None, "  "]
        )

        assert result == ["RELIANCE", "TCS"]


class TestStockRepositoryTopPicksLookup:
    @patch("app.repositories.stock_repository.supabase")
    def test_india_universe_is_split_into_bounded_requests(self, mock_supabase):
        recorded = _configure_stocks_lookup(
            mock_supabase, lambda or_filter: _rows_matching(or_filter, {"exchange": "NSE"})
        )

        StockRepository().get_many_by_symbol_for_top_picks(_symbols(IN_UNIVERSE_SIZE, "IN"))

        assert len(recorded) == 12
        assert max(len(item) for item in recorded) < MAX_SAFE_FILTER_LENGTH

    @patch("app.repositories.stock_repository.supabase")
    def test_usa_universe_is_split_into_bounded_requests(self, mock_supabase):
        recorded = _configure_stocks_lookup(
            mock_supabase, lambda or_filter: _rows_matching(or_filter, {"exchange": "NASDAQ"})
        )

        StockRepository().get_many_by_symbol_for_top_picks(_symbols(US_UNIVERSE_SIZE, "US"))

        assert len(recorded) == 43
        assert max(len(item) for item in recorded) < MAX_SAFE_FILTER_LENGTH

    @patch("app.repositories.stock_repository.supabase")
    def test_no_symbol_is_lost_across_batches(self, mock_supabase):
        recorded = _configure_stocks_lookup(
            mock_supabase, lambda or_filter: _rows_matching(or_filter, {"exchange": "NSE"})
        )

        symbols = _symbols(IN_UNIVERSE_SIZE, "IN")
        rows = StockRepository().get_many_by_symbol_for_top_picks(symbols)

        assert {row["symbol"] for row in rows} == set(symbols)
        assert len(rows) == len(symbols)

    @patch("app.repositories.stock_repository.supabase")
    def test_every_symbol_is_requested_exactly_once(self, mock_supabase):
        recorded = _configure_stocks_lookup(mock_supabase, lambda or_filter: [])

        StockRepository().get_many_by_symbol_for_top_picks(_symbols(IN_UNIVERSE_SIZE, "IN"))

        requested = [
            clause.split("symbol.eq.")[1]
            for item in recorded
            for clause in item.split(",")
        ]
        assert len(requested) == len(set(requested)) == IN_UNIVERSE_SIZE

    @patch("app.repositories.stock_repository.supabase")
    def test_duplicate_and_mixed_case_symbols_are_collapsed(self, mock_supabase):
        recorded = _configure_stocks_lookup(mock_supabase, lambda or_filter: [])

        StockRepository().get_many_by_symbol_for_top_picks(
            ["reliance", "RELIANCE", " tcs ", "RELIANCE"]
        )

        assert recorded == ["symbol.eq.RELIANCE,symbol.eq.TCS"]

    @patch("app.repositories.stock_repository.supabase")
    def test_empty_input_makes_no_request(self, mock_supabase):
        repository = StockRepository()

        assert repository.get_many_by_symbol_for_top_picks([]) == []
        assert repository.get_many_by_symbol_for_top_picks(["", "  "]) == []
        mock_supabase.table.assert_not_called()

    @patch("app.repositories.stock_repository.supabase")
    def test_selected_columns_stay_minimal(self, mock_supabase):
        selected = []

        def _select(columns):
            selected.append(columns)
            response = MagicMock()
            response.or_.return_value.eq.return_value.execute.return_value = (
                SimpleNamespace(data=[])
            )
            return response

        mock_supabase.table.return_value.select.side_effect = _select

        StockRepository().get_many_by_symbol_for_top_picks(["RELIANCE"])

        assert selected == ["symbol, exchange"]


class TestStockDataRepositoryTopPicksLookup:
    @patch("app.repositories.stock_data_repository.supabase")
    def test_india_universe_is_split_into_bounded_requests(self, mock_supabase):
        recorded = _configure_stock_data_lookup(mock_supabase, lambda or_filter: [])

        StockDataRepository().get_many_for_top_picks(_symbols(IN_UNIVERSE_SIZE, "IN"))

        assert len(recorded) == 12
        assert max(len(item) for item in recorded) < MAX_SAFE_FILTER_LENGTH

    @patch("app.repositories.stock_data_repository.supabase")
    def test_usa_universe_is_split_into_bounded_requests(self, mock_supabase):
        recorded = _configure_stock_data_lookup(mock_supabase, lambda or_filter: [])

        StockDataRepository().get_many_for_top_picks(_symbols(US_UNIVERSE_SIZE, "US"))

        assert len(recorded) == 43
        assert max(len(item) for item in recorded) < MAX_SAFE_FILTER_LENGTH

    @patch("app.repositories.stock_data_repository.supabase")
    def test_no_symbol_is_lost_across_batches(self, mock_supabase):
        _configure_stock_data_lookup(
            mock_supabase, lambda or_filter: _rows_matching(or_filter, {})
        )

        symbols = _symbols(US_UNIVERSE_SIZE, "US")
        rows = StockDataRepository().get_many_for_top_picks(symbols)

        assert {row["symbol"] for row in rows} == set(symbols)
        assert len(rows) == len(symbols)

    @patch("app.repositories.stock_data_repository.supabase")
    def test_empty_input_makes_no_request(self, mock_supabase):
        repository = StockDataRepository()

        assert repository.get_many_for_top_picks([]) == []
        assert repository.get_many_for_top_picks([None]) == []
        mock_supabase.table.assert_not_called()

    @patch("app.repositories.stock_data_repository.supabase")
    def test_selected_columns_stay_minimal(self, mock_supabase):
        selected = []

        def _select(columns):
            selected.append(columns)
            response = MagicMock()
            response.or_.return_value.execute.return_value = SimpleNamespace(data=[])
            return response

        mock_supabase.table.return_value.select.side_effect = _select

        StockDataRepository().get_many_for_top_picks(["RELIANCE"])

        assert selected == [
            "symbol, eligibility_json, score_json, cache_status, updated_at"
        ]


class TestBatchSizeIsSafe:
    @pytest.mark.parametrize("universe_size", [IN_UNIVERSE_SIZE, US_UNIVERSE_SIZE])
    def test_single_request_would_be_unsafe_but_batches_are_not(self, universe_size):
        symbols = _symbols(universe_size, "X")
        unsplit = ",".join("symbol.eq.%s" % symbol for symbol in symbols)

        assert len(unsplit) > MAX_SAFE_FILTER_LENGTH

        longest_batch = max(
            len(",".join("symbol.eq.%s" % symbol for symbol in batch))
            for batch in batched(symbols, SYMBOL_BATCH_SIZE)
        )
        assert longest_batch < MAX_SAFE_FILTER_LENGTH
