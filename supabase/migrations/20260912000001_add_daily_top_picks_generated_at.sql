-- Add generated_at column to daily_top_picks table for freshness tracking
-- This migration is additive only - does not drop any existing columns

ALTER TABLE public.daily_top_picks
ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ;

-- Backfill existing rows with current timestamp (they were generated in the past)
UPDATE public.daily_top_picks
SET generated_at = NOW()
WHERE generated_at IS NULL;

-- Add index for efficient lookups by country and generated_at
CREATE INDEX IF NOT EXISTS idx_daily_top_picks_country_generated_at
    ON public.daily_top_picks (country, generated_at DESC);

-- Grant privileges for service_role
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.daily_top_picks TO service_role;