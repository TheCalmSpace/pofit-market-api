"""Regression tests for the Top Picks egress bounds.

Ranking the whole universe from live Yahoo data costs roughly 258 KB per
symbol. For 3,566 NSE plus 8,456 US symbols that is about 3 GB per cycle,
because the scheduled ingestion job could only ever refresh a fraction of
the universe and everything else was therefore always stale at ranking time.

These tests pin the bounded behaviour:
  - the full universe is still ranked, from the cached score;
  - live full refreshes are capped;
  - symbols that have never been scored take priority within the cap, so a
    newly listed stock still reaches the ranking;
  - only the leading candidates get a quote refresh.
"""

from unittest.mock import MagicMock

from app.services.daily_top_picks_service import DailyTopPicksService


ELIGIBLE = {"financials_complete": True}


def score_payload(value):
    return {
        "eligibility_json": dict(ELIGIBLE),
        "score_json": {
            "overall_score": value,
            "growth_score": 1.0,
            "quality_score": 1.0,
            "financial_strength_score": 1.0,
            "valuation_score": 1.0,
        },
        "cache_status": "fresh",
        "updated_at": "2026-10-01T00:00:00",
    }


def build_service(n_symbols, cached, refresh_side_effect=None):
    service = DailyTopPicksService.__new__(DailyTopPicksService)
    service.stock_repo = MagicMock()
    service.stock_data_repo = MagicMock()
    service.market = MagicMock()
    service.alpha_filter = MagicMock()
    service.repository = MagicMock()
    service.logger = MagicMock()

    symbols = [f"S{i:03d}" for i in range(n_symbols)]

    service.stock_repo.list_all_by_country.return_value = [
        {"symbol": s, "company_name": "Company", "exchange": "NSE"} for s in symbols
    ]
    service.stock_repo.get_many_by_symbol_for_top_picks.return_value = [
        {"symbol": s, "exchange": "NSE"} for s in symbols
    ]
    service.stock_data_repo.get_many_for_top_picks.return_value = [
        {"symbol": row["symbol"], **row} for row in cached
    ]

    service.market._is_cache_valid.return_value = False
    if refresh_side_effect is None:
        service.market.refresh_stock.side_effect = lambda symbol, stock=None: score_payload(1.0)
    else:
        service.market.refresh_stock.side_effect = refresh_side_effect

    service.alpha_filter.is_eligible.return_value = (True, "Eligible")
    return service, symbols


class TestRefreshBudgetIsEnforced:
    def test_full_refreshes_are_capped(self):
        """50 symbols, no cached score, budget of 10 -> exactly 10 Yahoo calls."""
        service, _ = build_service(50, cached=[])

        rows = service.generate_country("IN", refresh_budget=10, quote_refresh_budget=0)

        assert service.market.refresh_stock.call_count == 10
        assert len(rows) == 10

    def test_capped_symbols_are_skipped_not_scored_from_nothing(self):
        service, symbols = build_service(20, cached=[])

        service.generate_country("IN", refresh_budget=5, quote_refresh_budget=0)

        refreshed = {c.args[0] for c in service.market.refresh_stock.call_args_list}
        assert len(refreshed) == 5

    def test_default_budget_is_far_below_the_universe(self):
        from app.services.daily_top_picks_service import (
            REFRESH_BUDGET,
            QUOTE_REFRESH_BUDGET,
        )

        assert REFRESH_BUDGET <= 100
        assert QUOTE_REFRESH_BUDGET <= 100


class TestRankingUsesTheWholeUniverse:
    def test_stale_cached_scores_still_rank_the_full_universe(self):
        """Stale cache must not silently drop symbols from the ranking.

        Previously a stale symbol triggered a live refresh, and a symbol with
        no score at all was skipped. Ranking now runs off the cached score, so
        the full universe competes for the top slots at zero egress cost.
        """
        cached = [
            {"symbol": f"S{i:03d}", **score_payload(float(i))}
            for i in range(30)
        ]
        service, _ = build_service(30, cached=cached)

        rows = service.generate_country("IN", refresh_budget=5, quote_refresh_budget=0)

        assert service.market.refresh_stock.call_count == 0
        assert len(rows) == 30
        assert rows[0]["symbol"] == "S029"
        assert rows[-1]["symbol"] == "S000"

    def test_symbol_without_a_cached_score_still_gets_refreshed(self):
        """A newly listed stock has no score yet, so it must take a slot in the
        refresh budget or it could never enter Alpha."""
        cached = [
            {"symbol": f"S{i:03d}", **score_payload(float(i))}
            for i in range(5)
        ]
        cached.append({"symbol": "S005", "eligibility_json": None, "score_json": None})
        service, _ = build_service(6, cached=cached)

        service.market.refresh_stock.side_effect = (
            lambda symbol, stock=None: score_payload(99.0)
        )

        rows = service.generate_country("IN", refresh_budget=10, quote_refresh_budget=0)

        service.market.refresh_stock.assert_called_once()
        assert service.market.refresh_stock.call_args.args[0] == "S005"
        assert rows[0]["symbol"] == "S005"


class TestQuoteRefresh:
    def test_only_leading_candidates_are_quote_refreshed(self):
        cached = [
            {"symbol": f"S{i:03d}", **score_payload(float(i))}
            for i in range(20)
        ]
        service, _ = build_service(20, cached=cached)

        service.generate_country("IN", refresh_budget=5, quote_refresh_budget=5)

        assert service.market.refresh_quote.call_count == 5
        refreshed = [c.args[0] for c in service.market.refresh_quote.call_args_list]
        assert refreshed == ["S019", "S018", "S017", "S016", "S015"]

    def test_quote_refresh_never_recomputes_the_score(self):
        """A quote-only update must not call the full refresh path."""
        cached = [{"symbol": "S000", **score_payload(50.0)}]
        service, _ = build_service(1, cached=cached)

        service.generate_country("IN", refresh_budget=1, quote_refresh_budget=1)

        service.market.refresh_stock.assert_not_called()
        service.market.refresh_quote.assert_called_once()

    def test_quote_refresh_failure_does_not_abort_the_run(self):
        cached = [
            {"symbol": f"S{i:03d}", **score_payload(float(i))}
            for i in range(5)
        ]
        service, _ = build_service(5, cached=cached)
        service.market.refresh_quote.side_effect = RuntimeError("yahoo down")

        rows = service.generate_country("IN", refresh_budget=1, quote_refresh_budget=5)

        assert len(rows) == 5


class TestEligibilityIsUnchanged:
    def test_ineligible_symbols_are_still_excluded(self):
        cached = [
            {"symbol": "S000", **score_payload(90.0)},
            {"symbol": "S001", **score_payload(80.0)},
        ]
        service, _ = build_service(2, cached=cached)
        service.alpha_filter.is_eligible.side_effect = [
            (False, "Below minimum market cap"),
            (True, "Eligible"),
        ]

        rows = service.generate_country("IN", refresh_budget=0, quote_refresh_budget=0)

        assert [r["symbol"] for r in rows] == ["S001"]

    def test_symbol_with_no_score_is_excluded(self):
        cached = [
            {"symbol": "S000", **score_payload(90.0)},
            {
                "symbol": "S001",
                "eligibility_json": dict(ELIGIBLE),
                "score_json": None,
            },
        ]
        service, _ = build_service(2, cached=cached)

        rows = service.generate_country("IN", refresh_budget=0, quote_refresh_budget=0)

        assert [r["symbol"] for r in rows] == ["S000"]
