ALTER TABLE public.portfolio_performance
ADD COLUMN IF NOT EXISTS as_of TIMESTAMPTZ;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'portfolio_performance'
          AND column_name = 'created_at'
    ) THEN
        UPDATE public.portfolio_performance
        SET as_of = created_at
        WHERE as_of IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'portfolio_performance'
          AND column_name = 'timestamp'
    ) THEN
        UPDATE public.portfolio_performance
        SET as_of = "timestamp"
        WHERE as_of IS NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'portfolio_performance'
          AND column_name = 'date'
    ) THEN
        UPDATE public.portfolio_performance
        SET as_of = "date"
        WHERE as_of IS NULL;
    END IF;
END $$;

DO $$
DECLARE
    null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO null_count
    FROM public.portfolio_performance
    WHERE as_of IS NULL;

    IF null_count > 0 THEN
        RAISE EXCEPTION 'Migration aborted: % rows in portfolio_performance have NULL as_of after backfill. Manual review required.', null_count;
    END IF;
END $$;

ALTER TABLE public.portfolio_performance
ALTER COLUMN as_of SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_portfolio_performance_market_as_of
    ON public.portfolio_performance (market, as_of DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_performance TO service_role;
