-- Add score_calculated_at column to stock_data table
-- This column tracks when the score was last calculated, separate from
-- quote freshness (updated_at). A quote-only refresh updates updated_at
-- but preserves score_calculated_at so stale scores are not masked as fresh.

ALTER TABLE public.stock_data
ADD COLUMN IF NOT EXISTS score_calculated_at timestamptz;

COMMENT ON COLUMN public.stock_data.score_calculated_at IS
'Timestamp when the overall_score was last computed. Preserved during quote-only refreshes to prevent stale scores from appearing fresh.';