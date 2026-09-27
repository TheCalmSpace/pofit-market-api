-- Extend portfolio_performance table for historical period reporting
-- Adds period column to distinguish between different time horizons

-- 1. Create period enum type
DO $$ BEGIN
    CREATE TYPE public.performance_period AS ENUM (
        '1M',
        '6M',
        '1Y',
        'INCEPTION',
        'FY_2025_26'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 2. Add period column to portfolio_performance
ALTER TABLE public.portfolio_performance
ADD COLUMN IF NOT EXISTS period public.performance_period NOT NULL DEFAULT 'INCEPTION';

-- 3. Add period to the existing index for efficient period-based queries
DROP INDEX IF EXISTS idx_portfolio_performance_market_as_of;
CREATE INDEX IF NOT EXISTS idx_portfolio_performance_market_period_as_of
    ON public.portfolio_performance (market, period, as_of DESC);

-- 4. Add unique constraint to prevent duplicate snapshots for same market/period/date
CREATE UNIQUE INDEX IF NOT EXISTS uniq_portfolio_performance_market_period_as_of
    ON public.portfolio_performance (market, period, as_of);

-- 5. Grant privileges
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_performance TO service_role;