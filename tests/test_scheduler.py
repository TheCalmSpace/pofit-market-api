import asyncio
from unittest.mock import MagicMock, patch

import pytest

import app.scheduler as scheduler_module


def _stopped_scheduler():
    scheduler = MagicMock()
    scheduler.running = False
    return scheduler


class TestSchedulerRegistration:
    def test_registers_independent_generation_and_reconciliation_jobs(self):
        scheduler = _stopped_scheduler()

        with patch.object(scheduler_module, "scheduler", scheduler):
            result = scheduler_module.start_scheduler()

        assert result is scheduler
        calls = scheduler.add_job.call_args_list
        assert [call.kwargs["id"] for call in calls] == [
            "india_top_picks",
            "india_alpha_reconciliation",
            "usa_top_picks",
            "usa_alpha_reconciliation",
        ]
        assert calls[0].args[0] is scheduler_module.generate_india
        assert calls[1].args[0] is scheduler_module.reconcile_india
        assert calls[2].args[0] is scheduler_module.generate_usa
        assert calls[3].args[0] is scheduler_module.reconcile_usa

        assert calls[0].kwargs["hour"] == 8
        assert calls[0].kwargs["minute"] == 0
        assert calls[1].kwargs["hour"] == 8
        assert calls[1].kwargs["minute"] == 30
        assert calls[2].kwargs["hour"] == 9
        assert calls[2].kwargs["minute"] == 30
        assert calls[3].kwargs["hour"] == 10
        assert calls[3].kwargs["minute"] == 0

        for call in calls:
            assert call.kwargs["replace_existing"] is True
            assert call.kwargs["coalesce"] is True
            assert call.kwargs["max_instances"] == 1

    def test_scheduler_timezone_is_kolkata(self):
        assert str(scheduler_module.scheduler.timezone) == "Asia/Kolkata"

    def test_does_not_duplicate_jobs_while_scheduler_is_running(self):
        scheduler = MagicMock()
        scheduler.running = True

        with patch.object(scheduler_module, "scheduler", scheduler):
            result = scheduler_module.start_scheduler()

        assert result is scheduler
        scheduler.add_job.assert_not_called()
        scheduler.start.assert_not_called()

    def test_shutdown_stops_scheduler(self):
        scheduler = MagicMock()
        scheduler.running = True

        with patch.object(scheduler_module, "scheduler", scheduler):
            scheduler_module.shutdown_scheduler()

        scheduler.shutdown.assert_called_once_with(wait=False)


class TestSchedulerJobs:
    def test_generate_india_only_generates_top_picks(self):
        with patch.object(scheduler_module, "DailyTopPicksService") as daily_service, patch.object(
            scheduler_module, "AlphaPortfolioService"
        ) as alpha_service, patch.object(
            scheduler_module, "PerformanceService"
        ) as performance_service:
            daily_service.return_value.generate_country.return_value = ["ABC"]

            scheduler_module.generate_india()

        daily_service.return_value.generate_country.assert_called_once_with("IN")
        alpha_service.assert_not_called()
        performance_service.assert_not_called()

    def test_generate_usa_only_generates_top_picks(self):
        with patch.object(scheduler_module, "DailyTopPicksService") as daily_service, patch.object(
            scheduler_module, "AlphaPortfolioService"
        ) as alpha_service, patch.object(
            scheduler_module, "PerformanceService"
        ) as performance_service:
            daily_service.return_value.generate_country.return_value = ["ABC"]

            scheduler_module.generate_usa()

        daily_service.return_value.generate_country.assert_called_once_with("US")
        alpha_service.assert_not_called()
        performance_service.assert_not_called()

    @pytest.mark.parametrize(
        ("reconcile", "market"),
        [
            (scheduler_module.reconcile_india, "IN"),
            (scheduler_module.reconcile_usa, "US"),
        ],
    )
    def test_reconciliation_does_not_generate_top_picks(self, reconcile, market):
        with patch.object(scheduler_module, "DailyTopPicksService") as daily_service, patch.object(
            scheduler_module, "AlphaPortfolioService"
        ) as alpha_service, patch.object(
            scheduler_module, "PerformanceService"
        ) as performance_service:
            alpha_service.return_value.reconcile_market.return_value = 2

            reconcile()

        daily_service.assert_not_called()
        alpha_service.return_value.reconcile_market.assert_called_once_with(market)
        performance_service.return_value.snapshot_market.assert_called_once_with(market)

    def test_reconciliation_skips_performance_when_alpha_fails(self, caplog):
        with patch.object(scheduler_module, "AlphaPortfolioService") as alpha_service, patch.object(
            scheduler_module, "PerformanceService"
        ) as performance_service:
            alpha_service.return_value.reconcile_market.side_effect = RuntimeError("failed")

            scheduler_module.reconcile_india()

        performance_service.return_value.snapshot_market.assert_not_called()
        assert "Alpha portfolio update failed for IN" in caplog.text


class TestSchedulerLifespan:
    @patch("app.main.shutdown_scheduler")
    @patch("app.main.start_scheduler")
    def test_lifespan_stops_scheduler_on_exit(self, start, shutdown):
        async def run_lifespan():
            from fastapi import FastAPI
            from app.main import lifespan

            async with lifespan(FastAPI()):
                pass

        asyncio.run(run_lifespan())

        start.assert_called_once()
        shutdown.assert_called_once()
