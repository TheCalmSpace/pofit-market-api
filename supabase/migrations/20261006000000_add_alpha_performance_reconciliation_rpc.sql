CREATE OR REPLACE FUNCTION public.apply_alpha_performance_reconciliation(
    p_actions jsonb
)
RETURNS TABLE (
    inserted integer,
    updated integer,
    deleted integer
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_action jsonb;
    v_proposed jsonb;
    v_kind text;
    v_market text;
    v_date date;
    v_row_id text;
    v_deleted_id text;
    v_row_count integer;
    v_inserted integer := 0;
    v_updated integer := 0;
    v_deleted integer := 0;
BEGIN
    IF p_actions IS NULL OR jsonb_typeof(p_actions) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'p_actions must be a JSON array';
    END IF;

    IF jsonb_array_length(p_actions) > 100 THEN
        RAISE EXCEPTION 'reconciliation plan exceeds the 100-action limit';
    END IF;

    LOCK TABLE public.portfolio_performance IN SHARE ROW EXCLUSIVE MODE;

    FOR v_action IN
        SELECT value FROM jsonb_array_elements(p_actions)
    LOOP
        v_kind := v_action->>'kind';
        v_market := upper(coalesce(v_action->>'market', ''));
        v_date := nullif(v_action->>'date', '')::date;

        IF v_market NOT IN ('IN', 'US') OR v_date IS NULL THEN
            RAISE EXCEPTION 'invalid market/date in reconciliation action';
        END IF;
        IF v_date < date '2026-09-14' OR v_date > date '2026-10-06' THEN
            RAISE EXCEPTION 'date % is outside the validated reconstruction window', v_date;
        END IF;

        PERFORM pg_advisory_xact_lock(
            hashtext('alpha_performance_backfill:' || v_market)
        );

        IF v_kind = 'INSERT' THEN
            v_proposed := v_action->'proposed';
            IF v_proposed IS NULL
                OR upper(coalesce(v_proposed->>'market', '')) IS DISTINCT FROM v_market
                OR nullif(v_proposed->>'date', '')::date IS DISTINCT FROM v_date
                OR (
                    nullif(v_proposed->>'as_of', '')::timestamptz
                    AT TIME ZONE 'UTC'
                )::date IS DISTINCT FROM v_date
                OR v_proposed->>'period' IS DISTINCT FROM 'INCEPTION'
            THEN
                RAISE EXCEPTION 'invalid proposed row for INSERT on %', v_date;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.portfolio_performance
                WHERE market = v_market
                  AND date = v_date
                  AND period::text = 'INCEPTION'
            ) THEN
                RAISE EXCEPTION 'INSERT conflict: a row already exists for % %',
                    v_market, v_date;
            END IF;

            INSERT INTO public.portfolio_performance (
                market,
                date,
                as_of,
                period,
                portfolio_nav,
                benchmark_nav,
                portfolio_return,
                benchmark_return,
                alpha,
                holdings_count
            )
            VALUES (
                v_market,
                v_date,
                (v_proposed->>'as_of')::timestamptz,
                (v_proposed->>'period')::public.performance_period,
                (v_proposed->>'portfolio_nav')::numeric,
                (v_proposed->>'benchmark_nav')::numeric,
                nullif(v_proposed->>'portfolio_return', '')::numeric,
                nullif(v_proposed->>'benchmark_return', '')::numeric,
                nullif(v_proposed->>'alpha', '')::numeric,
                (v_proposed->>'holdings_count')::integer
            );
            v_inserted := v_inserted + 1;
            IF (
                SELECT count(*)
                FROM public.portfolio_performance
                WHERE market = v_market
                  AND date = v_date
                  AND period::text = 'INCEPTION'
            ) <> 1 THEN
                RAISE EXCEPTION 'INSERT did not leave exactly one row for % %',
                    v_market, v_date;
            END IF;

        ELSIF v_kind = 'UPDATE' THEN
            v_proposed := v_action->'proposed';
            v_row_id := v_action->'existing'->>'id';
            IF v_row_id IS NULL
                OR v_proposed IS NULL
                OR upper(coalesce(v_proposed->>'market', '')) IS DISTINCT FROM v_market
                OR nullif(v_proposed->>'date', '')::date IS DISTINCT FROM v_date
                OR (
                    nullif(v_proposed->>'as_of', '')::timestamptz
                    AT TIME ZONE 'UTC'
                )::date IS DISTINCT FROM v_date
                OR v_proposed->>'period' IS DISTINCT FROM 'INCEPTION'
            THEN
                RAISE EXCEPTION 'invalid proposed row or existing id for UPDATE on %',
                    v_date;
            END IF;

            UPDATE public.portfolio_performance
            SET date = v_date,
                as_of = (v_proposed->>'as_of')::timestamptz,
                period = (v_proposed->>'period')::public.performance_period,
                portfolio_nav = (v_proposed->>'portfolio_nav')::numeric,
                benchmark_nav = (v_proposed->>'benchmark_nav')::numeric,
                portfolio_return = nullif(v_proposed->>'portfolio_return', '')::numeric,
                benchmark_return = nullif(v_proposed->>'benchmark_return', '')::numeric,
                alpha = nullif(v_proposed->>'alpha', '')::numeric,
                holdings_count = (v_proposed->>'holdings_count')::integer
            WHERE id::text = v_row_id
              AND market = v_market
              AND date = v_date
              AND period::text = 'INCEPTION';
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            IF v_row_count <> 1 THEN
                RAISE EXCEPTION 'UPDATE expected one row for % %, found %',
                    v_market, v_date, v_row_count;
            END IF;
            v_updated := v_updated + 1;

            IF jsonb_typeof(coalesce(v_action->'deleted_ids', '[]'::jsonb)) <> 'array' THEN
                RAISE EXCEPTION 'deleted_ids must be an array for UPDATE on %', v_date;
            END IF;
            FOR v_deleted_id IN
                SELECT jsonb_array_elements_text(
                    coalesce(v_action->'deleted_ids', '[]'::jsonb)
                )
            LOOP
                DELETE FROM public.portfolio_performance
                WHERE id::text = v_deleted_id
                  AND market = v_market
                  AND date = v_date
                  AND period::text = 'INCEPTION';
                GET DIAGNOSTICS v_row_count = ROW_COUNT;
                IF v_row_count <> 1 THEN
                    RAISE EXCEPTION 'duplicate DELETE expected one row for % %, found %',
                        v_market, v_date, v_row_count;
                END IF;
                v_deleted := v_deleted + 1;
            END LOOP;

            IF (
                SELECT count(*)
                FROM public.portfolio_performance
                WHERE market = v_market
                  AND date = v_date
                  AND period::text = 'INCEPTION'
            ) <> 1 THEN
                RAISE EXCEPTION 'UPDATE did not leave exactly one row for % %',
                    v_market, v_date;
            END IF;

        ELSIF v_kind = 'DELETE' THEN
            IF jsonb_typeof(coalesce(v_action->'deleted_ids', '[]'::jsonb)) <> 'array' THEN
                RAISE EXCEPTION 'deleted_ids must be an array for DELETE on %', v_date;
            END IF;
            FOR v_deleted_id IN
                SELECT jsonb_array_elements_text(
                    coalesce(v_action->'deleted_ids', '[]'::jsonb)
                )
            LOOP
                DELETE FROM public.portfolio_performance
                WHERE id::text = v_deleted_id
                  AND market = v_market
                  AND date = v_date
                  AND period::text = 'INCEPTION';
                GET DIAGNOSTICS v_row_count = ROW_COUNT;
                IF v_row_count <> 1 THEN
                    RAISE EXCEPTION 'DELETE expected one row for % %, found %',
                        v_market, v_date, v_row_count;
                END IF;
                v_deleted := v_deleted + 1;
            END LOOP;
            IF EXISTS (
                SELECT 1
                FROM public.portfolio_performance
                WHERE market = v_market
                  AND date = v_date
                  AND period::text = 'INCEPTION'
            ) THEN
                RAISE EXCEPTION 'DELETE left a row for % %', v_market, v_date;
            END IF;
        ELSE
            RAISE EXCEPTION 'unsupported reconciliation action %', v_kind;
        END IF;
    END LOOP;

    RETURN QUERY SELECT v_inserted, v_updated, v_deleted;
END;
$$;

REVOKE ALL ON FUNCTION public.apply_alpha_performance_reconciliation(jsonb)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_alpha_performance_reconciliation(jsonb)
    TO service_role;
