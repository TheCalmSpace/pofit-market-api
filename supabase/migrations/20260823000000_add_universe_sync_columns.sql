-- Add universe synchronization columns to stocks
-- ISIN as stable identity, status for NSE/application state,
-- first_listed_date for IPO tracking, last_synced_at for sync freshness.

ALTER TABLE public.stocks ADD COLUMN IF NOT EXISTS isin TEXT;
ALTER TABLE public.stocks ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ACTIVE';
ALTER TABLE public.stocks ADD COLUMN IF NOT EXISTS first_listed_date DATE;
ALTER TABLE public.stocks ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_stocks_isin
    ON public.stocks(isin) WHERE isin IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_stocks_status
    ON public.stocks(status);

CREATE INDEX IF NOT EXISTS idx_stocks_last_synced_at
    ON public.stocks(last_synced_at);
