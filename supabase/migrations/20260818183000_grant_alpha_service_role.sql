-- The API uses the Supabase service-role client for the Alpha repositories.
-- These tables were created without privileges for that role, causing every
-- Alpha read and reconciliation write to fail with PostgreSQL error 42501.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.alpha_portfolio TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.alpha_history TO service_role;

-- This table is not populated by the Alpha scheduler, but its repository is
-- served by the API and must not fail because of the same missing privilege.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.portfolio_performance TO service_role;
