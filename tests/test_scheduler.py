import time
from unittest.mock import MagicMock, patch

import pytest

from app.scheduler import (
    generate_india,
    generate_usa,
    ingest_stock_data,
    shutdown_scheduler,
    start_scheduler,
    sync_nse_universe,
)


class TestSchedulerLifecycle:
    def test_start_scheduler_creates_scheduler_and_jobs(self):
        scheduler = start_scheduler()

        assert scheduler is not None
        assert scheduler.running

        jobs = scheduler.get_jobs()
        job_ids = {job.id for job in jobs}
        assert "india_top_picks" in job_ids
        assert "usa_top_picks" in job_ids

        india_job = scheduler.get_job("india_top_picks")
        usa_job = scheduler.get_job("usa_top_picks")

        assert str(india_job.trigger.timezone) == "Asia/Kolkata"
        assert str(usa_job.trigger.timezone) == "Asia/Kolkata"

    def test_start_scheduler_idempotent(self):
        scheduler1 = start_scheduler()
        scheduler2 = start_scheduler()

        assert scheduler1 is scheduler2
        assert len(scheduler1.get_jobs()) == 4

    def test_shutdown_scheduler_stops_scheduler(self):
        scheduler = start_scheduler()
        assert scheduler.running

        shutdown_scheduler()

        assert not scheduler.running

    def test_shutdown_scheduler_idempotent(self):
        start_scheduler()
        shutdown_scheduler()
        shutdown_scheduler()  # Should not raise

    def test_scheduler_timezone_is_kolkata(self):
        scheduler = start_scheduler()
        assert str(scheduler.timezone) == "Asia/Kolkata"


class TestSchedulerJobs:
    @patch("app.scheduler.DailyTopPicksService")
    @patch("app.scheduler.AlphaPortfolioService")
    @patch("app.scheduler.PerformanceService")
    def test_generate_india_calls_services(self, mock_perf, mock_alpha, mock_daily):
        mock_daily_instance = MagicMock()
        mock_daily_instance.generate_country.return_value = ["pick1", "pick2"]
        mock_daily.return_value = mock_daily_instance

        mock_alpha_instance = MagicMock()
        mock_alpha_instance.reconcile_market.return_value = 5
        mock_alpha.return_value = mock_alpha_instance

        generate_india()

        mock_daily_instance.generate_country.assert_called_once_with("IN")
        mock_alpha_instance.reconcile_market.assert_called_once_with("IN")

    @patch("app.scheduler.DailyTopPicksService")
    @patch("app.scheduler.AlphaPortfolioService")
    @patch("app.scheduler.PerformanceService")
    def test_generate_usa_calls_services(self, mock_perf, mock_alpha, mock_daily):
        mock_daily_instance = MagicMock()
        mock_daily_instance.generate_country.return_value = ["pick1"]
        mock_daily.return_value = mock_daily_instance

        mock_alpha_instance = MagicMock()
        mock_alpha_instance.reconcile_market.return_value = 3
        mock_alpha.return_value = mock_alpha_instance

        generate_usa()

        mock_daily_instance.generate_country.assert_called_once_with("US")
        mock_alpha_instance.reconcile_market.assert_called_once_with("US")

    @patch("app.scheduler.DailyTopPicksService")
    @patch("app.scheduler.AlphaPortfolioService")
    @patch("app.scheduler.PerformanceService")
    def test_generate_india_handles_alpha_exception(self, mock_perf, mock_alpha, mock_daily, caplog):
        mock_daily_instance = MagicMock()
        mock_daily_instance.generate_country.return_value = ["pick1"]
        mock_daily.return_value = mock_daily_instance

        mock_alpha_instance = MagicMock()
        mock_alpha_instance.reconcile_market.side_effect = Exception("Alpha failed")
        mock_alpha.return_value = mock_alpha_instance

        generate_india()

        assert "Alpha portfolio update failed for IN" in caplog.text

    @patch("app.scheduler.DailyTopPicksService")
    @patch("app.scheduler.AlphaPortfolioService")
    @patch("app.scheduler.PerformanceService")
    def test_generate_usa_handles_alpha_exception(self, mock_perf, mock_alpha, mock_daily, caplog):
        mock_daily_instance = MagicMock()
        mock_daily_instance.generate_country.return_value = ["pick1"]
        mock_daily.return_value = mock_daily_instance

        mock_alpha_instance = MagicMock()
        mock_alpha_instance.reconcile_market.side_effect = Exception("Alpha failed")
        mock_alpha.return_value = mock_alpha_instance

        generate_usa()

        assert "Alpha portfolio update failed for US" in caplog.text


class TestSchedulerIntegration:
    def test_lifespan_startup_and_shutdown(self):
        """The lifespan starts and stops the scheduler and registers jobs.

        This test used to run the live BackgroundScheduler with real job
        targets. A job that fired executed the genuine generation code
        against the configured Supabase database, which wrote test rows
        into the production `portfolio_performance` table. `tests/conftest.py`
        now neutralises job execution, so this test verifies registration
        and lifecycle without touching a database.
        """
        from app.main import lifespan
        from fastapi import FastAPI

        app = FastAPI()

        async def test_lifespan():
            async with lifespan(app):
                from app.scheduler import _scheduler, _scheduler_started
                assert _scheduler is not None
                assert _scheduler_started
                assert _scheduler.running

                # Registration is preserved even though execution is
                # suppressed during tests.
                job_ids = {job.id for job in _scheduler.get_jobs()}
                assert {
                    "india_top_picks",
                    "usa_top_picks",
                    "stock_data_ingestion",
                    "nse_universe_sync",
                } <= job_ids

            from app.scheduler import _scheduler, _scheduler_started
            assert not _scheduler.running
            assert not _scheduler_started

        import asyncio
        asyncio.run(test_lifespan())

    def test_lifespan_does_not_run_real_jobs(self, scheduler_guard):
        """Real job targets must not be invoked by any test."""
        assert scheduler_guard.EXECUTED_TARGETS == []
        assert set(scheduler_guard.JOB_TARGETS) == {
            "generate_india",
            "generate_usa",
            "ingest_stock_data",
            "sync_nse_universe",
        }


class TestJobExecutionIsolation:
    def test_scheduled_job_never_executes_during_tests(self):
        """A job registered on the live scheduler must not be dispatched.

        This is the direct proof of the isolation guarantee: the scheduler
        is genuinely running and the job is genuinely due, yet the callable
        is never invoked. Before the guard, a job firing here would run the
        real generation code against the configured Supabase database.
        """
        from app.scheduler import scheduler, start_scheduler

        start_scheduler()
        assert scheduler.running

        calls = []

        def _probe():
            calls.append(1)

        job = scheduler.add_job(
            _probe,
            trigger="interval",
            seconds=1,
            id="test_isolation_probe",
            replace_existing=True,
        )
        assert job.id in {j.id for j in scheduler.get_jobs()}

        try:
            time.sleep(2.5)
        finally:
            try:
                scheduler.remove_job("test_isolation_probe")
            except Exception:
                pass

        assert calls == [], (
            "a scheduled job executed during tests; the isolation guard "
            "is not working"
        )

    def test_scheduler_executor_is_guarded(self):
        from app.scheduler import scheduler

        executors = getattr(scheduler, "_executors", {})
        assert "default" in executors
        assert type(executors["default"]).__name__ == "_NoopExecutor"

    def test_production_write_guard_blocks_inserts(self, probing_writes):
        """A production insert attempted during tests must be refused."""
        from app.core.supabase import supabase

        with probing_writes():
            table = supabase.table("portfolio_performance")
            with pytest.raises(AssertionError) as excinfo:
                table.insert({"market": "IN"}).execute()

        assert "refusing to run" in str(excinfo.value)

    def test_production_write_guard_blocks_updates_and_deletes(
        self, probing_writes
    ):
        from app.core.supabase import supabase

        with probing_writes():
            for method, args in (
                ("update", ({"market": "IN"},)),
                ("delete", ()),
                ("upsert", ({"market": "IN"},)),
            ):
                table = supabase.table("portfolio_performance")
                with pytest.raises(AssertionError):
                    getattr(table, method)(*args).execute()


class TestStockDataIngestionJob:
    @patch("app.scheduler.StockDataIngestionService")
    def test_ingest_stock_data_calls_service(self, mock_ingestion):
        mock_instance = MagicMock()
        mock_instance.run.return_value = MagicMock(
            total_universe=100,
            missing_cache=10,
            stale_cache=5,
            fresh_cache=85,
            processed=15,
            succeeded=14,
            failed=1,
            skipped=0,
            yahoo_rate_limited=False,
            duration_seconds=10.0,
            errors=[],
        )
        mock_ingestion.return_value = mock_instance

        ingest_stock_data()

        mock_instance.run.assert_called_once()

    @patch("app.scheduler.StockDataIngestionService")
    def test_ingest_stock_data_handles_exception(self, mock_ingestion, caplog):
        mock_instance = MagicMock()
        mock_instance.run.side_effect = Exception("Ingestion failed")
        mock_ingestion.return_value = mock_instance

        ingest_stock_data()

        assert "Ingestion failed" in caplog.text

    def test_scheduler_includes_ingestion_job(self):
        shutdown_scheduler()
        scheduler = start_scheduler()

        jobs = scheduler.get_jobs()
        job_ids = {job.id for job in jobs}

        assert "stock_data_ingestion" in job_ids
        assert "india_top_picks" in job_ids
        assert "usa_top_picks" in job_ids

        ingestion_job = scheduler.get_job("stock_data_ingestion")
        assert ingestion_job is not None
        assert str(ingestion_job.trigger.timezone) == "Asia/Kolkata"

        shutdown_scheduler()


class TestNSEUniverseSyncJob:
    def test_universe_sync_job_is_registered(self):
        shutdown_scheduler()
        scheduler = start_scheduler()

        job = scheduler.get_job("nse_universe_sync")
        assert job is not None

        shutdown_scheduler()

    def test_universe_sync_runs_daily_at_expected_time(self):
        shutdown_scheduler()
        scheduler = start_scheduler()

        job = scheduler.get_job("nse_universe_sync")
        fields = {f.name: str(f) for f in job.trigger.fields}

        assert job.trigger.__class__.__name__ == "CronTrigger"
        assert fields["hour"] == "5"
        assert fields["minute"] == "30"
        assert str(job.trigger.timezone) == "Asia/Kolkata"

        shutdown_scheduler()

    def test_universe_sync_is_scheduled_before_india_top_picks(self):
        shutdown_scheduler()
        scheduler = start_scheduler()

        sync = scheduler.get_job("nse_universe_sync")
        india = scheduler.get_job("india_top_picks")

        assert sync.next_run_time < india.next_run_time

        shutdown_scheduler()

    def test_universe_sync_does_not_overlap_other_jobs(self):
        shutdown_scheduler()
        scheduler = start_scheduler()

        job = scheduler.get_job("nse_universe_sync")

        assert job.coalesce is True
        assert job.max_instances == 1
        assert job.id == "nse_universe_sync"

        shutdown_scheduler()

    def test_universe_sync_startup_registers_existing_jobs(self):
        """A restart must not silently drop or duplicate existing jobs."""
        shutdown_scheduler()
        first = start_scheduler()
        assert first.get_job("nse_universe_sync") is not None
        shutdown_scheduler()

        second = start_scheduler()
        try:
            job_ids = [j.id for j in second.get_jobs()]
            assert job_ids.count("nse_universe_sync") == 1
            assert job_ids.count("stock_data_ingestion") == 1
            assert job_ids.count("india_top_picks") == 1
            assert job_ids.count("usa_top_picks") == 1
        finally:
            shutdown_scheduler()

    @patch("app.scheduler.UniverseSyncService")
    def test_sync_nse_universe_calls_run_once(self, mock_service):
        instance = MagicMock()
        instance.run.return_value = MagicMock(
            summary=MagicMock(
                return_value=(
                    "Sync complete: discovered=3574 new=8 updated=2783 "
                    "unchanged=769 newly_listed=8 failed=0 insert_failures=0 "
                    "nse_meta_requests=777 status=SUCCESS"
                )
            ),
            final_status="SUCCESS",
        )
        mock_service.return_value = instance

        sync_nse_universe()

        instance.run.assert_called_once_with()

    @patch("app.scheduler.UniverseSyncService")
    def test_sync_nse_universe_handles_exception(self, mock_service, caplog):
        instance = MagicMock()
        instance.run.side_effect = Exception("NSE bulk download failed")
        mock_service.return_value = instance

        sync_nse_universe()

        assert "NSE universe sync failed" in caplog.text
        assert "NSE bulk download failed" in caplog.text

    @patch("app.scheduler.UniverseSyncService")
    def test_sync_nse_universe_logs_failed_status(self, mock_service, caplog):
        instance = MagicMock()
        instance.run.return_value = MagicMock(
            summary=MagicMock(return_value="Sync complete: failed=5 status=FAILED"),
            final_status="FAILED",
        )
        mock_service.return_value = instance

        sync_nse_universe()

        assert "status FAILED" in caplog.text

    def test_sync_failure_does_not_stop_other_jobs(self, caplog):
        """An exploding sync job must leave the remaining jobs registered."""
        with patch("app.scheduler.UniverseSyncService") as mock_service:
            instance = MagicMock()
            instance.run.side_effect = Exception("boom")
            mock_service.return_value = instance

            shutdown_scheduler()
            scheduler = start_scheduler()
            try:
                job_ids = {j.id for j in scheduler.get_jobs()}
                assert "stock_data_ingestion" in job_ids
                assert "india_top_picks" in job_ids
                assert "usa_top_picks" in job_ids
                assert scheduler.running
            finally:
                shutdown_scheduler()


class TestExistingSchedulesUnchanged:
    """Guards that adding the universe sync did not move any existing job."""

    def test_india_top_picks_still_0800_ist(self):
        shutdown_scheduler()
        scheduler = start_scheduler()

        job = scheduler.get_job("india_top_picks")
        fields = {f.name: str(f) for f in job.trigger.fields}

        assert fields["hour"] == "8"
        assert fields["minute"] == "0"
        assert str(job.trigger.timezone) == "Asia/Kolkata"

        shutdown_scheduler()

    def test_usa_top_picks_still_0930_ist(self):
        shutdown_scheduler()
        scheduler = start_scheduler()

        job = scheduler.get_job("usa_top_picks")
        fields = {f.name: str(f) for f in job.trigger.fields}

        assert fields["hour"] == "9"
        assert fields["minute"] == "30"
        assert str(job.trigger.timezone) == "Asia/Kolkata"

        shutdown_scheduler()

    def test_stock_data_ingestion_still_every_4h_from_0600(self):
        from app.scheduler import INGESTION_HOUR, INGESTION_INTERVAL_HOURS, INGESTION_MINUTE

        assert INGESTION_HOUR == 6
        assert INGESTION_MINUTE == 0
        assert INGESTION_INTERVAL_HOURS == 4

        shutdown_scheduler()
        scheduler = start_scheduler()

        job = scheduler.get_job("stock_data_ingestion")
        assert str(job.trigger) == "interval[4:00:00]"

        shutdown_scheduler()

    @patch("app.scheduler.DailyTopPicksService")
    @patch("app.scheduler.AlphaPortfolioService")
    @patch("app.scheduler.PerformanceService")
    def test_alpha_generation_still_runs_for_both_markets(self, mock_perf, mock_alpha, mock_daily):
        """Alpha reconcile still runs inside both Top Picks jobs."""
        mock_daily.return_value.generate_country.return_value = ["p1"]
        mock_alpha.return_value.reconcile_market.return_value = 4

        generate_india()
        mock_alpha.return_value.reconcile_market.assert_called_once_with("IN")

        generate_usa()
        assert mock_alpha.return_value.reconcile_market.call_count == 2
        mock_alpha.return_value.reconcile_market.assert_called_with("US")