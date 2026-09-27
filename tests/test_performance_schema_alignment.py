"""Guards that the performance snapshot/report code matches the live schema.

Production `public.portfolio_performance` was missing `holdings_count` and
`period`. `PerformanceService.snapshot_market()` writes `holdings_count` and
`PerformanceService.report_performance()` selects it, so both the snapshot
INSERT and `/alpha/performance-report` failed with SQLSTATE 42703.
"""

import re
from pathlib import Path

from app.models import AlphaPerformanceReportResponse
from app.repositories.portfolio_performance_repository import (
    PortfolioPerformanceRepository,
)


MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"

HOLDINGS_COUNT_MIGRATION = MIGRATIONS / "20260928000000_add_portfolio_performance_holdings_count.sql"
PERIOD_MIGRATION = MIGRATIONS / "20260920000000_add_performance_periods.sql"

# Columns present in production before these migrations were applied.
PRE_EXISTING_COLUMNS = (
    "alpha",
    "as_of",
    "benchmark_nav",
    "benchmark_return",
    "created_at",
    "date",
    "id",
    "market",
    "portfolio_nav",
    "portfolio_return",
)


class TestHoldingsCountMigration:
    def test_migration_file_exists(self):
        assert HOLDINGS_COUNT_MIGRATION.is_file()

    def test_migration_adds_holdings_count_idempotently(self):
        sql = HOLDINGS_COUNT_MIGRATION.read_text(encoding="utf-8")

        assert "ALTER TABLE public.portfolio_performance" in sql
        assert re.search(
            r"ADD COLUMN IF NOT EXISTS holdings_count INTEGER", sql
        ), "holdings_count must be added with IF NOT EXISTS so re-runs are safe"

    def test_migration_does_not_drop_or_recreate_the_table(self):
        sql = HOLDINGS_COUNT_MIGRATION.read_text(encoding="utf-8").upper()

        for destructive in (
            "DROP TABLE",
            "DROP COLUMN",
            "TRUNCATE",
            "DELETE FROM",
        ):
            assert destructive not in sql, "migration must not contain %s" % destructive

    def test_migration_preserves_existing_value_columns(self):
        sql = HOLDINGS_COUNT_MIGRATION.read_text(encoding="utf-8")

        for column in ("portfolio_nav", "benchmark_nav", "portfolio_return", "benchmark_return"):
            assert "ALTER COLUMN %s" % column not in sql


class TestPeriodMigration:
    def test_period_migration_file_exists(self):
        assert PERIOD_MIGRATION.is_file()

    def test_period_migration_adds_period_idempotently(self):
        sql = PERIOD_MIGRATION.read_text(encoding="utf-8")

        assert "ADD COLUMN IF NOT EXISTS period" in sql


class TestCodeMatchesSchema:
    def test_performance_service_writes_holdings_count(self):
        source = (
            MIGRATIONS.parents[1] / "app" / "services" / "performance_service.py"
        ).read_text(encoding="utf-8")

        assert '"holdings_count"' in source

    def test_report_selects_holdings_count(self):
        source = (
            MIGRATIONS.parents[1] / "app" / "services" / "performance_service.py"
        ).read_text(encoding="utf-8")

        assert re.search(r'"[^"]*holdings_count[^"]*"', source)

    def test_response_model_exposes_holdings_count_as_int(self):
        assert "holdings_count" in AlphaPerformanceReportResponse.model_fields
        assert AlphaPerformanceReportResponse.model_fields["holdings_count"].annotation is int

    def test_repository_only_queries_migrated_columns(self):
        service_source = (
            MIGRATIONS.parents[1] / "app" / "services" / "performance_service.py"
        ).read_text(encoding="utf-8")
        repo_source = (
            MIGRATIONS.parents[1]
            / "app"
            / "repositories"
            / "portfolio_performance_repository.py"
        ).read_text(encoding="utf-8")

        available = set(PRE_EXISTING_COLUMNS) | {"holdings_count", "period"}

        referenced = set(
            re.findall(r'"(market|as_of|portfolio_nav|benchmark_nav|holdings_count|period|alpha|date|created_at|id|portfolio_return|benchmark_return)"',
                       service_source + repo_source)
        )
        unknown = referenced - available
        assert unknown == set(), "code references non-existent columns: %s" % unknown

    def test_repository_table_is_unchanged(self):
        assert PortfolioPerformanceRepository.TABLE == "portfolio_performance"
