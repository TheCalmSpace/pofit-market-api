-- Add missing `as_of` column to portfolio_performance table
-- The repository and service code expect an `as_of` TIMESTAMPTZ column
-- Production table was created manually without this column, causing SQLSTATE 42703
-- This migration is strictly additive/non-destructive: it does not drop any existing columns.

-- 1. Add as_of column if it doesn't exist
ALTER TABLE public.portfolio_performance
ADD COLUMN IF NOT EXISTS as_of TIMESTAMPTZ;

-- 2. Backfill as_of from created_at where as_of IS NULL (if created_at exists)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'portfolio_performance' AND column_name = 'created_at'
    ) THEN
        UPDATE public.portfolio_performance
        SET as_of = created_at
        WHERE as_of IS NULL;
    END IF;
END $$;

-- 3. Backfill as_of from timestamp where as_of IS NULL (if timestamp exists)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'portfolio_performance' AND column_name = 'timestamp'
    ) THEN
        UPDATE public.portfolio_performance
        SET as_of = timestamp
        WHERE as_of IS NULL;
    END IF;
END $$;

-- 4. Verify no NULL as_of values remain before enforcing NOT NULL.
-- If any NULL rows exist after backfill, the migration stops with an error
-- rather than inventing timestamps. This is intentional: manual review required.
DO $$
DECLARE
    null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO null_count
    FROM public.portfolio_performance
    WHERE as_of IS NULL;

    IF null_count > 0 THEN
        RAISE EXCEPTION 'Migration aborted: % rows in portfolio_performance have NULL as_of after backfill. Manual review required before enforcing NOT NULL.', null_count;
    END IF;
END $$;

-- 5. Enforce NOT NULL now that all rows have a value
ALTER TABLE public.portfolio_performance
ALTER COLUMN as_of SET NOT NULL;

-- 6. Add index for common query patterns (latest by market, history by market)
CREATE INDEX IF NOT EXISTS idx_portfolio_performance_market_as_of
    ON public.portfolio_performance (market, as_of DESC);

-- 7. Grant privileges for service_role (idempotent, matches existing grant migration)
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_performance TO service_role;