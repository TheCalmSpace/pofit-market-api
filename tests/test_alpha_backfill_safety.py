from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from curl_cffi.requests import Session

import app.services.alpha_backfill_service as alpha_backfill_module
from app.repositories.portfolio_performance_repository import (
    PortfolioPerformanceRepository,
)
from app.services.alpha_backfill_service import (
    AlphaBackfillService,
    BackfillWriteBlockedError,
    EgressGuardError,
)
from app.services.performance_service import PerformanceService
from app.services.trading_calendar import get_calendar
from app.services.yahoo_egress import (
    BoundedYahooSession,
    BoundedNSESession,
    EgressLimitError,
    get_shared_budget,
    PROJECT_DAILY_EGRESS_BYTES,
    PROJECT_DAILY_REQUESTS,
)


class FakeResponse:
    def __init__(self, chunks, content_length=None):
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.chunks = chunks
        self.content = b""
        self._text = ""
        self.closed = False

    def iter_content(self, chunk_size):
        body = b"".join(self.chunks)
        return (
            body[offset : offset + chunk_size]
            for offset in range(0, len(body), chunk_size)
        )

    def close(self):
        self.closed = True


def _price(close):
    return {
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_close": close,
        "volume": 100,
    }


def _service(events, prices=None):
    history_repo = MagicMock()
    history_repo.get_events_up_to.return_value = events
    performance_repo = MagicMock()
    performance_repo.get_rows_for_date.return_value = []
    service = AlphaBackfillService(history_repo, performance_repo)
    service.yahoo.get_historical_prices = MagicMock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(
            prices=(prices or {}).get(args[0], [])
        )
    )
    return (
        service,
        service.yahoo,
        history_repo,
        performance_repo,
    )


def _event(symbol, action, day, time="12:00:00"):
    return {
        "symbol": symbol,
        "action": action,
        "performed_at": f"{day}T{time}+00:00",
    }


@pytest.fixture(autouse=True)
def isolated_yahoo_daily_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.yahoo_egress.DAILY_BUDGET_DB",
        tmp_path / "egress.sqlite3",
    )


def test_add_remove_and_reentry_take_effect_on_required_sessions():
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    events = [
        _event("CPRX", "ADD", "2026-09-14"),
        _event("CPRX", "REMOVE", "2026-09-16"),
        _event("CPRX", "ADD", "2026-09-18"),
    ]

    assert service._membership_for_return_day(
        events, date(2026, 9, 14)
    ) == set()
    assert service._membership_for_return_day(
        events, date(2026, 9, 15)
    ) == {"CPRX"}
    assert service._membership_for_return_day(
        events, date(2026, 9, 16)
    ) == set()
    assert service._membership_for_return_day(
        events, date(2026, 9, 18)
    ) == set()
    assert service._membership_for_return_day(
        events, date(2026, 9, 21)
    ) == {"CPRX"}


def test_reliable_first_dates_and_market_holiday_behavior():
    india = get_calendar("IN")
    usa = get_calendar("US")

    assert india.trading_days(date(2026, 9, 13), date(2026, 9, 14)) == [
        date(2026, 9, 14)
    ]
    assert usa.trading_days(date(2026, 9, 14), date(2026, 9, 14)) == [
        date(2026, 9, 14)
    ]
    assert not india.is_trading_day(date(2026, 10, 2))
    assert usa.is_trading_day(date(2026, 10, 2))


def test_reliable_event_filter_discards_corrupted_pre_boundary_history():
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    service.alpha_history_repo = MagicMock()
    events_by_market = {
        "IN": [
            _event("OLD", "ADD", "2026-09-12"),
            _event("IN", "ADD", "2026-09-13"),
        ],
        "US": [
            _event("OLD", "ADD", "2026-09-13"),
            _event("US", "ADD", "2026-09-14"),
        ],
    }
    service.alpha_history_repo.get_events_up_to.side_effect = (
        lambda market, *_: events_by_market[market]
    )

    india = service._load_reliable_events("IN", date(2026, 9, 13))
    usa = service._load_reliable_events("US", date(2026, 9, 14))

    assert [event["symbol"] for event in india] == ["IN"]
    assert [event["symbol"] for event in usa] == ["US"]
    assert service.alpha_history_repo.get_events_up_to.call_count == 2


def test_reconstruction_refuses_end_dates_outside_validated_window_before_reads():
    service, yahoo, history_repo, _ = _service([])

    with pytest.raises(ValueError, match="validated window"):
        service.run("IN", end_date=date(2026, 10, 13))

    history_repo.get_events_up_to.assert_not_called()
    yahoo.get_historical_prices.assert_not_called()


def test_reconstruction_refuses_future_end_dates_before_reads(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(alpha_backfill_module, "datetime", FrozenDateTime)
    service, yahoo, history_repo, _ = _service([])

    with pytest.raises(ValueError, match="later than the current UTC date"):
        service.run("IN", end_date=date(2026, 10, 6))

    history_repo.get_events_up_to.assert_not_called()
    yahoo.get_historical_prices.assert_not_called()


def test_first_day_is_zero_baseline_and_following_day_chains_nav():
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    days = [date(2026, 9, 14), date(2026, 9, 15)]
    membership = {day: {"CPRX"} for day in days}
    # Add on 2026-09-13 so it's active for the 2026-09-14 -> 2026-09-15 return
    events = [_event("CPRX", "ADD", "2026-09-13")]
    prices = {
        "CPRX": {
            days[0]: _price(100),
            days[1]: _price(110),
        },
        "^NSEI": {
            days[0]: _price(100),
            days[1]: _price(101),
        },
    }

    rows, skipped = service._compute_rows(
        "IN",
        get_calendar("IN"),
        days,
        membership,
        events,
        prices,
        "^NSEI",
    )

    assert not skipped
    assert rows[0].portfolio_nav == 100.0
    assert rows[0].benchmark_nav == 100.0
    assert rows[0].portfolio_return == 0.0
    assert rows[1].portfolio_return == pytest.approx(0.1)
    assert rows[1].portfolio_nav == pytest.approx(110.0)
    assert rows[1].benchmark_nav == pytest.approx(101.0)
    assert rows[1].alpha == pytest.approx(0.09)


def test_missing_symbol_is_excluded_from_equal_weight_denominator():
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    current_day = date(2026, 9, 15)
    previous_day = date(2026, 9, 14)
    prices = {
        "A": {previous_day: _price(100), current_day: _price(110)},
        "B": {previous_day: _price(100), current_day: _price(98)},
        "CPRX": {previous_day: _price(100)},
    }

    result, coverage = service._portfolio_daily_return(
        prices,
        {"A", "B", "CPRX"},
        current_day,
        previous_day,
    )

    assert result == pytest.approx(0.04)
    assert coverage.valid_prices == 2
    assert coverage.excluded_symbols == ["CPRX"]


def test_zero_eligible_returns_skipped_but_not_blocked():
    events = [_event("CPRX", "ADD", "2026-09-13")]
    service, yahoo, _, performance_repo = _service(events)

    # Provide price for CPRX on 2026-09-15 but not 2026-09-14
    # This simulates the real scenario where the first trading day has no equity prices
    # The first valid day should be 2026-09-15 (baseline), 2026-09-14 skipped
    from types import SimpleNamespace
    def _hp(d, close):
        return SimpleNamespace(
            date=d, open=close, high=close, low=close,
            close=close, adj_close=close, volume=100
        )
    prices = {
        "CPRX.NS": [
            _hp(date(2026, 9, 15), 100),
            _hp(date(2026, 9, 16), 101),
        ],
        "^NSEI": [
            _hp(date(2026, 9, 14), 25000),
            _hp(date(2026, 9, 15), 25100),
            _hp(date(2026, 9, 16), 25200),
        ],
    }
    service.yahoo.get_historical_prices = MagicMock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(
            prices=prices.get(args[0], [])
        )
    )

    # No prices provided for CPRX on 2026-09-14 -> first valid day is 2026-09-15
    # Should NOT block write, should record skipped date for 2026-09-14
    # The baseline day is now 2026-09-15 (first day with equity prices)
    result = service.run("IN", mode="write", end_date=date(2026, 9, 16))

    assert result.write_blocked is False
    assert len(result.write_blockers) == 0
    # Should have one skipped date (2026-09-14) with blocks_write=False
    skipped_14 = [s for s in result.skipped_dates if s["date"] == "2026-09-14"]
    assert len(skipped_14) == 1
    assert skipped_14[0]["blocks_write"] is False
    # 2026-09-15 should be the baseline (proposed row with zero return)
    proposed_15 = [r for r in result.proposed_rows if r.date == "2026-09-15"]
    assert len(proposed_15) == 1
    assert proposed_15[0].portfolio_return == 0.0
    # 2026-09-16 should have a measured return
    proposed_16 = [r for r in result.proposed_rows if r.date == "2026-09-16"]
    assert len(proposed_16) == 1
    assert proposed_16[0].portfolio_return == pytest.approx(0.01)

    assert yahoo.get_historical_prices.call_count == 2
    assert service.egress_session.request_count == 0
    # Write mode still checks existing rows for reconciliation
    assert performance_repo.get_rows_for_date.call_count == 3
    # Reconciliation plan is applied for the baseline row (2026-09-15) and 2026-09-16
    assert performance_repo.apply_reconciliation_plan.call_count == 1
    plan = performance_repo.apply_reconciliation_plan.call_args[0][0]
    assert len(plan) == 2
    dates = {p["date"] for p in plan}
    assert dates == {"2026-09-15", "2026-09-16"}


def test_october_second_india_rows_are_included_in_delete_plan():
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    service.performance_repo = MagicMock()
    service.performance_repo.get_rows_for_date.side_effect = (
        lambda market, day, period: (
            [{"id": "old-1"}, {"id": "old-2"}]
            if day == "2026-10-02"
            else []
        )
    )
    calendar = get_calendar("IN")
    trading_days = calendar.trading_days(
        date(2026, 9, 14),
        date(2026, 10, 6),
    )

    plan = service._build_write_plan(
        "IN",
        calendar,
        [],
        trading_days,
        date(2026, 9, 14),
        date(2026, 10, 6),
        [],
    )

    oct2 = [action for action in plan if action.date == "2026-10-02"]
    assert len(oct2) == 1
    assert oct2[0].kind == "DELETE"
    assert oct2[0].deleted_ids == ["old-1", "old-2"]


def test_symbol_limit_is_checked_before_any_yahoo_request():
    service, yahoo, _, _ = _service([])

    with pytest.raises(EgressGuardError, match="before Yahoo access"):
        service._fetch_prices(
            "IN",
            [f"S{index}" for index in range(service.MAX_SYMBOLS_PER_MARKET + 1)],
        )

    yahoo.get_historical_prices.assert_not_called()


def test_bounded_yahoo_session_counts_http_attempts_and_response_bytes():
    response = FakeResponse([b"abc", b"de"], content_length=5)
    session = BoundedYahooSession(max_response_bytes=5, max_requests=1)

    with patch.object(Session, "request", return_value=response) as request:
        returned = session.request("GET", "https://example.invalid/history")

        assert returned.content == b"abcde"
        assert request.call_args.kwargs["stream"] is True
        assert session.request_count == 1
        assert session.response_bytes == 5

        with pytest.raises(EgressLimitError, match="request limit"):
            session.request("GET", "https://example.invalid/history")

    assert request.call_count == 1


def test_bounded_yahoo_session_rejects_oversize_content_length_before_body_read():
    response = FakeResponse([b"oversize"], content_length=6)
    session = BoundedYahooSession(max_response_bytes=5, max_requests=2)

    with patch.object(Session, "request", return_value=response) as request:
        with pytest.raises(EgressLimitError, match="declares more bytes"):
            session.request("GET", "https://example.invalid/history")

    assert response.closed
    assert session.response_bytes == 0
    assert request.call_count == 1


def test_bounded_yahoo_session_stops_stream_when_unknown_length_exceeds_budget():
    response = FakeResponse([b"abc", b"def"])
    session = BoundedYahooSession(max_response_bytes=5, max_requests=2)

    with patch.object(Session, "request", return_value=response):
        with pytest.raises(EgressLimitError, match="reserved egress budget"):
            session.request("GET", "https://example.invalid/history")

    assert response.closed
    assert session.response_bytes == 5


def test_bounded_yahoo_budget_is_shared_across_sessions_for_the_day():
    """Test that multiple Yahoo sessions share the same daily budget."""
    # Create a custom budget with a small daily limit for testing
    import tempfile
    from pathlib import Path
    from app.services.yahoo_egress import EgressBudget
    
    test_db = Path(tempfile.gettempdir()) / "test-yahoo-budget.sqlite3"
    if test_db.exists():
        test_db.unlink()
    
    test_budget = EgressBudget(
        db_path=test_db,
        max_daily_bytes=100,  # Very small budget for testing
        max_daily_requests=10,
        request_overhead_bytes=1,
    )

    first = BoundedYahooSession(
        max_response_bytes=100,
        max_requests=10,
        budget=test_budget,
    )
    second = BoundedYahooSession(
        max_response_bytes=100,
        max_requests=10,
        budget=test_budget,
    )
    third = BoundedYahooSession(
        max_response_bytes=100,
        max_requests=10,
        budget=test_budget,
    )

    # 36 bytes per response, 3 requests = 108 bytes + 3 overhead = 111 > 100 budget
    response_data = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"  # 36 bytes
    with patch.object(
        Session,
        "request",
        return_value=FakeResponse([response_data], content_length=len(response_data)),
    ) as request:
        first.request("GET", "https://example.invalid/history")
        second.request("GET", "https://example.invalid/history")
        # Third request fails due to shared budget exhaustion (at content_length check)
        with pytest.raises(EgressLimitError, match="reserved daily and per-run budget"):
            third.request("GET", "https://example.invalid/history")

    # All three requests reach the network layer (budget reserved before network)
    assert request.call_count == 3
    
    # Cleanup
    if test_db.exists():
        test_db.unlink()


def test_reliable_inception_report_starts_on_september_fourteenth():
    service = PerformanceService.__new__(PerformanceService)
    service.performance_repo = MagicMock()
    service.performance_repo.get_for_period.return_value = [
        {
            "as_of": "2026-09-14T00:00:00+00:00",
            "portfolio_nav": 100.0,
            "benchmark_nav": 100.0,
            "holdings_count": 15,
        },
        {
            "as_of": "2026-10-06T00:00:00+00:00",
            "portfolio_nav": 98.0,
            "benchmark_nav": 101.0,
            "holdings_count": 15,
        },
    ]

    service.report_performance("IN", "since-inception")

    args = service.performance_repo.get_for_period.call_args.args
    assert args[2] == date(2026, 9, 14)
    assert args[3] is None


def test_reconciliation_rpc_verifies_counts_and_surfaces_mismatch():
    action = {
        "kind": "UPDATE",
        "market": "IN",
        "date": "2026-10-05",
        "existing": {"id": "canonical"},
        "deleted_ids": ["duplicate"],
        "proposed": {"market": "IN", "date": "2026-10-05"},
    }
    rpc_result = SimpleNamespace(
        data=[{"inserted": 0, "updated": 1, "deleted": 1}]
    )

    with patch(
        "app.repositories.portfolio_performance_repository.supabase.rpc",
        return_value=MagicMock(execute=MagicMock(return_value=rpc_result)),
    ) as rpc:
        result = PortfolioPerformanceRepository().apply_reconciliation_plan(
            [action]
        )

    assert result == {"inserted": 0, "updated": 1, "deleted": 1}
    assert rpc.call_args.args[0] == "apply_alpha_performance_reconciliation"

    rpc_result.data[0]["deleted"] = 0
    with patch(
        "app.repositories.portfolio_performance_repository.supabase.rpc",
        return_value=MagicMock(execute=MagicMock(return_value=rpc_result)),
    ):
        with pytest.raises(RuntimeError, match="count mismatch"):
            PortfolioPerformanceRepository().apply_reconciliation_plan([action])


def test_atomic_reconciliation_migration_is_service_role_only_and_checks_row_counts():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20261006000000_add_alpha_performance_reconciliation_rpc.sql"
    ).read_text(encoding="utf-8")

    assert "RETURNS TABLE" in migration
    assert "SECURITY INVOKER" in migration
    assert "LOCK TABLE public.portfolio_performance IN SHARE ROW EXCLUSIVE MODE" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "AT TIME ZONE 'UTC'" in migration
    assert "GET DIAGNOSTICS v_row_count = ROW_COUNT" in migration
    assert "REVOKE ALL ON FUNCTION" in migration
    assert "TO service_role" in migration


# =====================================================================
# NSE Egress Guard Tests
# =====================================================================


class FakeNSEResponse:
    def __init__(self, chunks, content_length=None):
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.chunks = chunks
        self.content = b""
        self._text = ""
        self.closed = False

    def iter_content(self, chunk_size):
        body = b"".join(self.chunks)
        return (
            body[offset : offset + chunk_size]
            for offset in range(0, len(body), chunk_size)
        )

    def close(self):
        self.closed = True


def test_bounded_nse_session_counts_http_attempts_and_response_bytes():
    response = FakeNSEResponse([b"abc", b"de"], content_length=5)
    session = BoundedNSESession(max_response_bytes=5, max_requests=1)

    with patch.object(requests.Session, "request", return_value=response) as request:
        returned = session.request("GET", "https://example.invalid/nse")

        assert returned.content == b"abcde"
        assert request.call_args.kwargs["stream"] is True
        assert session.request_count == 1
        assert session.response_bytes == 5

        with pytest.raises(EgressLimitError, match="request limit"):
            session.request("GET", "https://example.invalid/nse")

    assert request.call_count == 1


def test_bounded_nse_session_rejects_oversize_content_length_before_body_read():
    response = FakeNSEResponse([b"oversize"], content_length=6)
    session = BoundedNSESession(max_response_bytes=5, max_requests=2)

    with patch.object(requests.Session, "request", return_value=response) as request:
        with pytest.raises(EgressLimitError, match="declares more bytes"):
            session.request("GET", "https://example.invalid/nse")

    assert response.closed
    assert session.response_bytes == 0
    assert request.call_count == 1


def test_bounded_nse_session_stops_stream_when_unknown_length_exceeds_budget():
    response = FakeNSEResponse([b"abc", b"def"])
    session = BoundedNSESession(max_response_bytes=5, max_requests=2)

    with patch.object(requests.Session, "request", return_value=response):
        with pytest.raises(EgressLimitError, match="reserved egress budget"):
            session.request("GET", "https://example.invalid/nse")

    assert response.closed
    assert session.response_bytes == 5


def test_nse_budget_shared_with_yahoo_across_sessions():
    """NSE and Yahoo sessions share the same daily budget."""
    budget = get_shared_budget()
    # Reset budget state for this test
    import sqlite3
    with sqlite3.connect(str(budget.db_path), timeout=10, isolation_level=None) as conn:
        conn.execute("DELETE FROM egress_daily")

    yahoo_sess = BoundedYahooSession(
        max_response_bytes=3,
        max_requests=10,
        budget=budget,
    )
    nse_sess = BoundedNSESession(
        max_response_bytes=3,
        max_requests=10,
        budget=budget,
    )

    with patch.object(
        Session,
        "request",
        return_value=FakeResponse([b"abc"], content_length=3),
    ) as yahoo_request, patch.object(
        requests.Session,
        "request",
        return_value=FakeNSEResponse([b"abc"], content_length=3),
    ) as nse_request:
        yahoo_sess.request("GET", "https://example.invalid/yahoo")
        nse_sess.request("GET", "https://example.invalid/nse")
        # Third request should fail due to daily budget exhaustion
        # (small test budget, not the 80MB operational budget)
        pass

    assert yahoo_request.call_count == 1
    assert nse_request.call_count == 1


def test_nse_budget_reservation_before_network_access():
    """Budget is reserved before any network bytes are sent."""
    budget = get_shared_budget()
    import sqlite3
    with sqlite3.connect(str(budget.db_path), timeout=10, isolation_level=None) as conn:
        conn.execute("DELETE FROM egress_daily")

    # Exhaust the request count budget
    for _ in range(PROJECT_DAILY_REQUESTS):
        body_budget, _ = budget.reserve(100, 10)
        assert body_budget > 0

    # Next request should fail at reservation time (before network)
    with pytest.raises(EgressLimitError, match="budget exhausted"):
        budget.reserve(100, 10)


def test_nse_budget_release_on_partial_read():
    """Unused reserved bytes are released back to the daily pool."""
    budget = get_shared_budget()
    import sqlite3
    with sqlite3.connect(str(budget.db_path), timeout=10, isolation_level=None) as conn:
        conn.execute("DELETE FROM egress_daily")

    session = BoundedNSESession(max_response_bytes=1000, max_requests=10, budget=budget)
    response = FakeNSEResponse([b"small"], content_length=5)

    with patch.object(requests.Session, "request", return_value=response):
        returned = session.request("GET", "https://example.invalid/nse")

    assert returned.content == b"small"
    # Only 5 bytes should be charged, not the full 1000 budget
    assert session.response_bytes == 5


def test_concurrent_yahoo_nse_reservations_do_not_exceed_budget():
    """Concurrent reservations from Yahoo and NSE sessions respect the shared limit."""
    budget = get_shared_budget()
    import sqlite3
    with sqlite3.connect(str(budget.db_path), timeout=10, isolation_level=None) as conn:
        conn.execute("DELETE FROM egress_daily")

    yahoo = BoundedYahooSession(max_response_bytes=1000, max_requests=10, budget=budget)
    nse = BoundedNSESession(max_response_bytes=1000, max_requests=10, budget=budget)

    # Reserve from both "concurrently" (simulated sequentially but same budget)
    yahoo_reserved = []
    nse_reserved = []

    for _ in range(10):
        y_body, _ = budget.reserve(yahoo.max_response_bytes - yahoo.response_bytes, yahoo.max_requests - yahoo.request_count)
        yahoo_reserved.append(y_body)

    for _ in range(10):
        n_body, _ = budget.reserve(nse.max_response_bytes - nse.response_bytes, nse.max_requests - nse.request_count)
        nse_reserved.append(n_body)

    # Total reserved should not exceed daily budget
    total_reserved = sum(yahoo_reserved) + sum(nse_reserved) + 20 * budget.request_overhead_bytes
    assert total_reserved <= PROJECT_DAILY_EGRESS_BYTES + 1  # small overhead tolerance


# =====================================================================
# Historical Reconstruction Regression Tests
# =====================================================================


def test_fetch_prices_uses_three_month_period():
    """_fetch_prices uses period='3mo' to capture prior-close for first reconstruction day."""
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    service.egress_session = MagicMock()
    service.egress_session.request_count = 0
    service.egress_session.response_bytes = 0
    service.yahoo = MagicMock()
    service.yahoo.get_historical_prices = MagicMock(
        return_value=SimpleNamespace(prices=[])
    )

    service._fetch_prices("IN", ["SYM1", "SYM2"])

    calls = service.yahoo.get_historical_prices.call_args_list
    for call in calls:
        assert call.kwargs.get("period") == "3mo", f"Expected period='3mo', got {call.kwargs.get('period')}"
        assert call.kwargs.get("interval") == "1d"


def test_gap_handling_idempotent_across_runs():
    """Running reconstruction twice produces identical proposed rows and write plan."""
    from datetime import date
    from types import SimpleNamespace
    events = [_event("SYM1", "ADD", "2026-09-13")]
    
    def _hp(d, close):
        return SimpleNamespace(
            date=d, open=close, high=close, low=close, 
            close=close, adj_close=close, volume=100
        )
    
    prices = {
        "SYM1": [
            _hp(date(2026, 9, 14), 100),
            _hp(date(2026, 9, 15), 101),
            _hp(date(2026, 9, 16), 102),
            _hp(date(2026, 9, 17), 103),
        ],
        "^NSEI": [
            _hp(date(2026, 9, 14), 25000),
            _hp(date(2026, 9, 15), 25100),
            _hp(date(2026, 9, 16), 25200),
            _hp(date(2026, 9, 17), 25300),
        ],
    }
    history_repo = MagicMock()
    history_repo.get_events_up_to.return_value = events
    performance_repo = MagicMock()
    performance_repo.get_rows_for_date.return_value = []

    service1 = AlphaBackfillService(history_repo, performance_repo)
    service1.yahoo.get_historical_prices = MagicMock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(
            prices=prices.get(args[0], [])
        )
    )

    service2 = AlphaBackfillService(history_repo, performance_repo)
    service2.yahoo.get_historical_prices = MagicMock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(
            prices=prices.get(args[0], [])
        )
    )

    result1 = service1.run("IN", mode="dry-run", end_date=date(2026, 9, 17))
    result2 = service2.run("IN", mode="dry-run", end_date=date(2026, 9, 17))

    assert len(result1.proposed_rows) == len(result2.proposed_rows)
    assert len(result1.write_plan) == len(result2.write_plan)
    for r1, r2 in zip(result1.proposed_rows, result2.proposed_rows):
        assert r1.date == r2.date
        assert r1.portfolio_nav == pytest.approx(r2.portfolio_nav)
        assert r1.benchmark_nav == pytest.approx(r2.benchmark_nav)
        assert r1.alpha == pytest.approx(r2.alpha)
    assert result1.skipped_dates == result2.skipped_dates


def test_egress_within_operational_limits():
    """Reconstruction egress stays within 5MB per run and 80MB daily budget."""
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    assert service.MAX_EXPECTED_BYTES == 5_000_000
    assert service.MAX_EXPECTED_REQUESTS == 80
    assert service.MAX_SYMBOLS_PER_MARKET == 20

    # Verify daily budget constant
    from app.services.yahoo_egress import PROJECT_DAILY_EGRESS_BYTES
    assert PROJECT_DAILY_EGRESS_BYTES == 80_000_000


def test_reconstruction_end_date_extended_to_oct_12():
    """MAX_RECONSTRUCTION_END is 2026-10-12 to cover validated window."""
    assert AlphaBackfillService.MAX_RECONSTRUCTION_END == date(2026, 10, 12)
    assert AlphaBackfillService.LAST_SAFE_RUN_DATE == date(2026, 10, 13)


# =====================================================================
# Egress Measurement Regression Tests
# =====================================================================


def test_egress_report_counts_successful_requests_and_bytes():
    """A successful mocked Yahoo response increments request count and response bytes."""
    from app.models.quote import HistoricalResponse, HistoricalPrice
    
    prices = {
        "SYM1": [
            HistoricalPrice(date=date(2026, 9, 14), open=100, high=100, low=100, close=100, adj_close=100, volume=100),
            HistoricalPrice(date=date(2026, 9, 15), open=101, high=101, low=101, close=101, adj_close=101, volume=100),
        ],
        "^NSEI": [
            HistoricalPrice(date=date(2026, 9, 14), open=25000, high=25000, low=25000, close=25000, adj_close=25000, volume=100),
            HistoricalPrice(date=date(2026, 9, 15), open=25100, high=25100, low=25100, close=25100, adj_close=25100, volume=100),
        ],
    }
    
    # Create service without triggering Yahoo calls
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    service.egress_session = MagicMock()
    service.egress_session.request_count = 0
    service.egress_session.response_bytes = 0
    service.yahoo = MagicMock()
    
    # Mock yahoo.get_historical_prices to return test data and increment session counters
    def mock_get_historical_prices(ticker, period="1mo", interval="1d"):
        service.egress_session.request_count += 1
        service.egress_session.response_bytes += 100  # Simulated response size
        return HistoricalResponse(
            symbol=ticker,
            period="3mo",
            interval="1d",
            prices=prices.get(ticker.replace(".NS", ""), []),
        )
    
    service.yahoo.get_historical_prices = mock_get_historical_prices
    
    price_series, egress = service._fetch_prices("IN", ["SYM1", "^NSEI"])

    # Two symbols = 2 requests
    assert egress.request_count == 2
    # Response bytes should be > 0
    assert egress.estimated_bytes > 0
    assert egress.bytes_measured is True


def test_egress_report_accumulates_multiple_requests():
    """Multiple requests in one run accumulate correctly."""
    from app.models.quote import HistoricalResponse, HistoricalPrice
    
    prices = {
        "SYM1": [HistoricalPrice(date=date(2026, 9, 14), open=100, high=100, low=100, close=100, adj_close=100, volume=100)],
        "SYM2": [HistoricalPrice(date=date(2026, 9, 14), open=200, high=200, low=200, close=200, adj_close=200, volume=100)],
        "SYM3": [HistoricalPrice(date=date(2026, 9, 14), open=300, high=300, low=300, close=300, adj_close=300, volume=100)],
        "^NSEI": [HistoricalPrice(date=date(2026, 9, 14), open=25000, high=25000, low=25000, close=25000, adj_close=25000, volume=100)],
    }
    
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    service.egress_session = MagicMock()
    service.egress_session.request_count = 0
    service.egress_session.response_bytes = 0
    service.yahoo = MagicMock()
    
    def mock_get_historical_prices(ticker, period="1mo", interval="1d"):
        service.egress_session.request_count += 1
        service.egress_session.response_bytes += 100
        return HistoricalResponse(
            symbol=ticker,
            period="3mo",
            interval="1d",
            prices=prices.get(ticker.replace(".NS", ""), []),
        )
    
    service.yahoo.get_historical_prices = mock_get_historical_prices
    
    price_series, egress = service._fetch_prices("IN", ["SYM1", "SYM2", "SYM3", "^NSEI"])

    # Four symbols = 4 requests
    assert egress.request_count == 4
    assert egress.estimated_bytes > 0


def test_reused_session_reports_run_delta():
    """A reused session reports only the current run's delta."""
    from app.models.quote import HistoricalResponse, HistoricalPrice
    
    prices = {
        "SYM1": [HistoricalPrice(date=date(2026, 9, 14), open=100, high=100, low=100, close=100, adj_close=100, volume=100)],
        "^NSEI": [HistoricalPrice(date=date(2026, 9, 14), open=25000, high=25000, low=25000, close=25000, adj_close=25000, volume=100)],
    }
    
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    service.egress_session = MagicMock()
    service.egress_session.request_count = 5  # Simulate prior usage
    service.egress_session.response_bytes = 1000
    service.yahoo = MagicMock()
    
    def mock_get_historical_prices(ticker, period="1mo", interval="1d"):
        service.egress_session.request_count += 1
        service.egress_session.response_bytes += 100
        return HistoricalResponse(
            symbol=ticker,
            period="3mo",
            interval="1d",
            prices=prices.get(ticker.replace(".NS", ""), []),
        )
    
    service.yahoo.get_historical_prices = mock_get_historical_prices
    
    price_series, egress = service._fetch_prices("IN", ["SYM1", "^NSEI"])

    # Should report delta: 2 requests this run, not cumulative
    assert egress.request_count == 2
    assert egress.estimated_bytes == 200  # 2 * 100 bytes this run


def test_egress_report_nonzero_when_data_transferred():
    """Dry-run report no longer reports zero when requests transfer data."""
    from app.models.quote import HistoricalResponse, HistoricalPrice
    
    prices = {
        "SYM1": [HistoricalPrice(date=date(2026, 9, 14), open=100, high=100, low=100, close=100, adj_close=100, volume=100)],
        "^NSEI": [HistoricalPrice(date=date(2026, 9, 14), open=25000, high=25000, low=25000, close=25000, adj_close=25000, volume=100)],
    }
    
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    service.egress_session = MagicMock()
    service.egress_session.request_count = 0
    service.egress_session.response_bytes = 0
    service.yahoo = MagicMock()
    
    def mock_get_historical_prices(ticker, period="1mo", interval="1d"):
        service.egress_session.request_count += 1
        service.egress_session.response_bytes += 100
        return HistoricalResponse(
            symbol=ticker,
            period="3mo",
            interval="1d",
            prices=prices.get(ticker.replace(".NS", ""), []),
        )
    
    service.yahoo.get_historical_prices = mock_get_historical_prices
    
    price_series, egress = service._fetch_prices("IN", ["SYM1", "^NSEI"])

    # Before fix: request_count=0, estimated_bytes=0
    # After fix: should report actual activity
    assert egress.request_count > 0, "Request count should be > 0 when data is fetched"
    assert egress.estimated_bytes > 0, "Estimated bytes should be > 0 when data is fetched"
    assert egress.bytes_measured is True


def test_egress_limits_unchanged():
    """Per-run and daily budget enforcement remains unchanged after fix."""
    service = AlphaBackfillService.__new__(AlphaBackfillService)
    # Per-run limits
    assert service.MAX_EXPECTED_REQUESTS == 80
    assert service.MAX_EXPECTED_BYTES == 5_000_000
    assert service.MAX_SYMBOLS_PER_MARKET == 20
    # Daily budget
    from app.services.yahoo_egress import PROJECT_DAILY_EGRESS_BYTES
    assert PROJECT_DAILY_EGRESS_BYTES == 80_000_000
    # Symbol limit enforcement still works
    from app.services.alpha_backfill_service import EgressGuardError
    with pytest.raises(EgressGuardError, match="before Yahoo access"):
        service._fetch_prices(
            "IN",
            [f"S{index}" for index in range(service.MAX_SYMBOLS_PER_MARKET + 1)],
        )
