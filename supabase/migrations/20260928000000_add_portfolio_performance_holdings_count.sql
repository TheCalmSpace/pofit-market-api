-- Add missing `holdings_count` column to portfolio_performance table
--
-- `PerformanceService.snapshot_market()` writes `holdings_count` on every
-- snapshot and `PerformanceService.report_performance()` selects it in
-- app/services/performance_service.py. The production table was created
-- manually without the column, so both the snapshot INSERT and the
-- /alpha/performance-report SELECT failed with SQLSTATE 42703
-- ("column portfolio_performance.holdings_count does not exist").
--
-- This migration is strictly additive/non-destructive: it does not drop or
-- recreate the table and does not touch portfolio_nav, benchmark_nav,
-- portfolio_return or benchmark_return.
--
-- The `period` column is added by 20260920000000_add_performance_periods.sql,
-- which must be applied first (or at least before this migration is used by a
-- reporting query).

-- 1. Add holdings_count column if it doesn't exist.
-- A count of holdings is a whole number, so INTEGER with a 0 default matches
-- both the writer (len(rows) / 0) and the reader (AlphaPerformanceReportResponse.
-- holdings_count: int).
ALTER TABLE public.portfolio_performance
ADD COLUMN IF NOT EXISTS holdings_count INTEGER NOT NULL DEFAULT 0;

-- 2. Backfill any pre-existing rows that were inserted before this column
-- existed, so historical snapshots report a real value instead of NULL.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'portfolio_performance'
          AND column_name = 'created_at'
    ) THEN
        UPDATE public.portfolio_performance
        SET holdings_count = 0
        WHERE holdings_count IS NULL;
    END IF;
END $$;

-- 3. Guard privileges for service_role (idempotent, matches the existing
-- grant migrations). The INSERT in PortfolioPerformanceRepository.insert_snapshot
-- depends on this.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_performance TO service_role;
