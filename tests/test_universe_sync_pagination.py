"""Tests for NSE universe sync pagination, insert payload and failure reporting.

Regression coverage for three production defects:

1. `_fetch_existing_universe()` read the NSE universe without pagination.
   PostgREST returns at most 1000 rows, and the production NSE universe is
   2,386 rows, so every stock outside the first page was invisible to the
   existing-stock lookup and re-classified as newly listed on each run.
2. The INSERT payload carried a `series` key, but `public.stocks` has no
   `series` column, so every new-listing INSERT failed with SQLSTATE 42703 and
   no new listing was ever stored.
3. INSERT errors were swallowed into `failed_rows`, which downgraded the run to
   PARTIAL_SUCCESS while the scheduled workflow still exited zero.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.nse_client import NSEClient
from app.services.universe_sync_service import (
    UNIVERSE_PAGE_SIZE,
    UniverseSyncService,
)


SYNC_PATCH = "app.services.universe_sync_service.supabase"

# Columns exposed by the production `public.stocks` table, read from the live
# PostgREST schema. The INSERT payload must be a subset of these.
LIVE_STOCKS_COLUMNS = {
    "id",
    "company_name",
    "country",
    "created_at",
    "exchange",
    "first_listed_date",
    "industry",
    "is_active",
    "isin",
    "last_synced_at",
    "sector",
    "status",
    "symbol",
}

LISTED_META = {"isDelisted": "false", "isSuspended": "false"}


def _existing_row(index):
    return {
        "id": "uuid-%04d" % index,
        "symbol": "SYM%05d" % index,
        "company_name": "COMPANY %05d" % index,
        "isin": "INE%011d" % index,
        "exchange": "NSE",
        "status": "LISTED",
        "last_synced_at": "2026-09-26T00:00:00+00:00",
    }


def _page(start, stop):
    return [_existing_row(index) for index in range(start, stop)]


def _make_supabase_mock(pages, insert_side_effect=None):
    """Supabase mock serving a paginated `stocks` read.

    `pages` is a list of row lists; page N is returned for
    `.range(N * page_size, ...)`. The final page may be shorter.
    """
    supabase_mock = MagicMock()
    requested_ranges = []

    builder = supabase_mock.table.return_value.select.return_value.eq.return_value

    def _range(offset, end):
        requested_ranges.append((offset, end))
        page_index = offset // UNIVERSE_PAGE_SIZE
        rows = pages[page_index] if page_index < len(pages) else []
        response = MagicMock()
        response.execute.return_value = SimpleNamespace(data=rows)
        return response

    builder.range.side_effect = _range

    if insert_side_effect is None:
        supabase_mock.table.return_value.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "new"}])
        )
    else:
        supabase_mock.table.return_value.insert.return_value.execute.side_effect = (
            insert_side_effect
        )

    supabase_mock.table.return_value.update.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": "uuid-0000"}])
    )

    return supabase_mock, requested_ranges


@contextmanager
def _installed(supabase_mock):
    """Install the Supabase mock into the module under test."""
    with patch(SYNC_PATCH, supabase_mock):
        yield


def _make_service(pages, sec_list, insert_side_effect=None, meta=None):
    """Build a UniverseSyncService with a mocked NSE client and Supabase mock.

    Returns `(service, supabase_mock, requested_ranges)`. Wrap calls in
    `_installed(supabase_mock)`.
    """
    nse_client = MagicMock(spec=NSEClient)
    nse_client.fetch_sec_list.return_value = sec_list
    nse_client.fetch_active_securities_isin.return_value = {}
    nse_client.equity_meta_info.return_value = dict(
        LISTED_META, **({"isin": "INE000000001"} if meta is None else meta)
    )

    supabase_mock, requested_ranges = _make_supabase_mock(
        pages, insert_side_effect=insert_side_effect
    )

    return UniverseSyncService(nse_client=nse_client), supabase_mock, requested_ranges


class TestExistingUniversePagination:
    def test_universe_larger_than_one_page_is_fully_loaded(self):
        service, supabase_mock, requested_ranges = _make_service(
            [_page(0, UNIVERSE_PAGE_SIZE), _page(UNIVERSE_PAGE_SIZE, 2_386)],
            sec_list=[],
        )

        with _installed(supabase_mock):
            existing = service._fetch_existing_universe()

        assert len(existing["all"]) == 2_386
        assert requested_ranges == [
            (0, UNIVERSE_PAGE_SIZE - 1),
            (UNIVERSE_PAGE_SIZE, 2 * UNIVERSE_PAGE_SIZE - 1),
            (2 * UNIVERSE_PAGE_SIZE, 3 * UNIVERSE_PAGE_SIZE - 1),
        ]

    def test_pagination_stops_on_a_short_page(self):
        service, supabase_mock, requested_ranges = _make_service(
            [_page(0, 50)], sec_list=[]
        )

        with _installed(supabase_mock):
            existing = service._fetch_existing_universe()

        assert len(existing["all"]) == 50
        assert requested_ranges == [(0, UNIVERSE_PAGE_SIZE - 1)]

    def test_pagination_stops_on_an_empty_page(self):
        service, supabase_mock, requested_ranges = _make_service([[]], sec_list=[])

        with _installed(supabase_mock):
            existing = service._fetch_existing_universe()

        assert existing["all"] == []
        assert requested_ranges == [(0, UNIVERSE_PAGE_SIZE - 1)]

    def test_rows_are_not_duplicated_across_pages(self):
        service, supabase_mock, _ = _make_service(
            [_page(0, UNIVERSE_PAGE_SIZE), _page(UNIVERSE_PAGE_SIZE, 2_000)],
            sec_list=[],
        )

        with _installed(supabase_mock):
            existing = service._fetch_existing_universe()

        ids = [row["id"] for row in existing["all"]]
        assert len(ids) == len(set(ids)) == 2_000
        assert len(existing["by_symbol"]) == len(existing["all"])
        assert len(existing["by_isin"]) == len(existing["all"])

    def test_stock_on_a_later_page_is_not_reinserted(self):
        """A stock beyond the 1000-row cap must be matched, not re-inserted."""
        beyond_cap = _existing_row(1_500)

        service, supabase_mock, _ = _make_service(
            [_page(0, UNIVERSE_PAGE_SIZE), _page(UNIVERSE_PAGE_SIZE, 2_386)],
            sec_list=[
                {
                    "Symbol": beyond_cap["symbol"],
                    "Series": "EQ",
                    "Security Name": beyond_cap["company_name"],
                }
            ],
            meta={"isin": beyond_cap["isin"]},
        )

        with _installed(supabase_mock):
            report = service.run()

        assert report.new_securities == 0
        assert report.newly_listed == 0
        assert report.final_status == "SUCCESS"
        supabase_mock.table.return_value.insert.assert_not_called()


class TestInsertPayload:
    def test_payload_does_not_contain_series(self):
        service = UniverseSyncService(nse_client=MagicMock(spec=NSEClient))

        payload = service._build_sync_payload(
            {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"},
            dict(LISTED_META, isin="INENEW001"),
            {},
        )

        assert "series" not in payload
        assert payload["symbol"] == "NEWCO"
        assert payload["status"] == "LISTED"

    def test_payload_columns_all_exist_in_live_stocks_table(self):
        service = UniverseSyncService(nse_client=MagicMock(spec=NSEClient))

        payload = service._build_sync_payload(
            {"Symbol": "NEWCO", "Series": "SM", "Security Name": "New Company Limited"},
            dict(
                LISTED_META,
                isin="INENEW001",
                sector="Technology",
                industry="Software",
            ),
            {},
        )

        unknown = set(payload) - LIVE_STOCKS_COLUMNS
        assert unknown == set(), (
            "payload writes columns absent from production: %s" % unknown
        )

    def test_insert_of_a_new_listing_sends_no_series_key(self):
        service, supabase_mock, _ = _make_service(
            [[]],
            sec_list=[
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"}
            ],
            meta={"isin": "INENEW001"},
        )

        with _installed(supabase_mock):
            report = service.run()

        assert report.new_securities == 1
        assert report.newly_listed == 1
        assert report.final_status == "SUCCESS"

        inserted = supabase_mock.table.return_value.insert.call_args[0][0]
        assert "series" not in inserted
        assert inserted["status"] == "NEWLY_LISTED"

    def test_insert_payload_matches_the_production_column_set(self):
        service, supabase_mock, _ = _make_service(
            [[]],
            sec_list=[
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"}
            ],
            meta={"isin": "INENEW001"},
        )

        with _installed(supabase_mock):
            service.run()

        inserted = supabase_mock.table.return_value.insert.call_args[0][0]
        assert set(inserted) <= LIVE_STOCKS_COLUMNS


class TestInsertFailuresAreLoud:
    def test_insert_failure_forces_failed_status(self):
        service, supabase_mock, _ = _make_service(
            [[]],
            sec_list=[
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"}
            ],
            insert_side_effect=Exception(
                '{"code":"42703","message":"column stocks.series does not exist"}'
            ),
            meta={"isin": "INENEW001"},
        )

        with _installed(supabase_mock):
            report = service.run()

        assert report.insert_failures == 1
        assert report.new_securities == 0
        assert report.final_status == "FAILED"
        assert any("series does not exist" in error for error in report.errors)

    def test_insert_failure_is_not_downgraded_to_partial_success(self):
        """A successful update must not mask a failed insert."""
        existing = _existing_row(0)

        service, supabase_mock, _ = _make_service(
            [[existing]],
            sec_list=[
                {"Symbol": existing["symbol"], "Series": "EQ", "Security Name": "Renamed Co"},
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"},
            ],
            insert_side_effect=Exception(
                "duplicate key value violates unique constraint"
            ),
        )

        def _meta(symbol):
            if symbol == existing["symbol"]:
                return dict(LISTED_META, isin=existing["isin"])
            return dict(LISTED_META, isin="INENEW001")

        service.nse.equity_meta_info.side_effect = _meta

        with _installed(supabase_mock):
            report = service.run()

        assert report.updated_securities == 1
        assert report.insert_failures == 1
        assert report.final_status == "FAILED"

    def test_summary_reports_insert_failures(self):
        service, supabase_mock, _ = _make_service(
            [[]],
            sec_list=[
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"}
            ],
            insert_side_effect=Exception("boom"),
            meta={"isin": "INENEW001"},
        )

        with _installed(supabase_mock):
            report = service.run()

        assert "insert_failures=1" in report.summary()
        assert "status=FAILED" in report.summary()

    def test_one_failed_insert_does_not_abort_the_remaining_rows(self):
        service, supabase_mock, _ = _make_service(
            [[]],
            sec_list=[
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"},
                {"Symbol": "NEWCO2", "Series": "EQ", "Security Name": "New Company Two"},
            ],
            insert_side_effect=Exception("boom"),
            meta={},
        )

        with _installed(supabase_mock):
            report = service.run()

        assert report.insert_failures == 2
        assert report.final_status == "FAILED"

    def test_successful_run_is_still_reported_as_success(self):
        service, supabase_mock, _ = _make_service(
            [[]],
            sec_list=[
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"}
            ],
            meta={"isin": "INENEW001"},
        )

        with _installed(supabase_mock):
            report = service.run()

        assert report.insert_failures == 0
        assert report.final_status == "SUCCESS"

    def test_dry_run_never_reports_insert_failures(self):
        service, supabase_mock, _ = _make_service(
            [[]],
            sec_list=[
                {"Symbol": "NEWCO", "Series": "EQ", "Security Name": "New Company Limited"}
            ],
            meta={"isin": "INENEW001"},
        )
        service.dry_run = True

        with _installed(supabase_mock):
            report = service.run()

        assert report.newly_listed == 1
        assert report.insert_failures == 0
        assert report.final_status == "SUCCESS"
        supabase_mock.table.return_value.insert.assert_not_called()
        supabase_mock.table.return_value.update.assert_not_called()
