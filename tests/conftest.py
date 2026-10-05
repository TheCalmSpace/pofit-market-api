"""Test isolation guard for the APScheduler-backed services.

The application lifespan calls `start_scheduler()`, which registers the
real scheduled jobs and starts a live BackgroundScheduler. If a job fires
while tests are running, the real job function executes: it calls
`DailyTopPicksService`, `AlphaPortfolioService`, `PerformanceService`,
`StockDataIngestionService` or `UniverseSyncService` against whatever
Supabase credentials are in the environment. Locally that is the
production database, so a test run silently wrote 12 rows into
`portfolio_performance` on 2026-10-02.

This module removes that hazard without touching production scheduling.
Job registration, trigger configuration, timezone and scheduler
lifecycle are all unchanged; only *execution* is neutralised.

Two independent layers:

1. A no-op executor replaces the scheduler's `default` executor, so a job
   that reaches its run time is accepted by the scheduler and then
   discarded. The function registered for the job is never called.

2. The job target names in `app.scheduler` are wrapped with recorders, so
   any attempt to invoke a real job raises loudly instead of quietly
   writing to a database.

Layer 2 is safe for the existing scheduler unit tests because those tests
import `generate_india` and friends into their own module namespace at
collection time. Conftest fixtures run after collection, so those tests
keep calling the genuine functions, while `start_scheduler()` - which
resolves the names at call time - picks up the recorders.

The decisive guard is the Supabase write block below. Suppressing job
execution alone was not enough: the scheduler tests call `generate_india()`
directly, and those functions construct a real `PerformanceService()` that
writes a performance snapshot. That path wrote 6 rows into the production
`portfolio_performance` table on 2026-10-02 with no scheduler job firing
at all. The write block closes it from any direction.
"""

from contextlib import contextmanager

import pytest
from apscheduler.executors.base import BaseExecutor

# Job targets that must never run for real during tests.
JOB_TARGETS = (
    "generate_india",
    "generate_usa",
    "ingest_stock_data",
    "sync_nse_universe",
)

# Jobs the scheduler asked to run while tests were active. Non-empty means
# the scheduler really did fire during the run.
SUBMITTED_JOBS = []

# Real job targets that were actually invoked. Must stay empty.
EXECUTED_TARGETS = []

# Mutating Supabase operations attempted while tests were active.
BLOCKED_WRITES = []

# Outbound market-data calls attempted while tests were active. A test that
# reaches a real provider is both a correctness problem and an egress problem:
# the daily market-data budget is a production invariant, and a test run that
# calls Yahoo spends it.
BLOCKED_PROVIDER_CALLS = []

# Yahoo methods that reach the network. Construction and the pure helpers
# (`_with_transient_retries`, `_is_transient_provider_error`, ...) are left
# usable so the existing unit tests of retry and error-classification logic
# keep working. Patching the `YahooService` name in a service module replaces
# the class entirely, so tests that mock the provider are unaffected.
_NETWORK_METHODS = (
    "get_quote",
    "get_historical_prices",
    "get_financials",
    "get_financial_history",
    "search_symbols",
)

# Depth of deliberate guard probes. A probe verifies the guard works and
# must not count as an accidental write attempt.
_PROBE_DEPTH = 0

_MUTATIONS = ("insert", "upsert", "update", "delete")


@pytest.fixture
def probing_writes():
	"""Context manager factory that allows deliberate guard probes.

	Exposed as a fixture rather than imported by tests: importing
	`tests.conftest` from a test module creates a second module object with
	its own globals, so the probe flag would not be seen by the guard. A
	fixture guarantees the same module instance is used.
	"""

	@contextmanager
	def _probe():
		global _PROBE_DEPTH
		_PROBE_DEPTH += 1
		try:
			yield
		finally:
			_PROBE_DEPTH -= 1

	return _probe


@pytest.fixture
def scheduler_guard():
	"""Read-only view of guard state, for assertions in tests."""

	class _GuardView:
		EXECUTED_TARGETS = EXECUTED_TARGETS
		SUBMITTED_JOBS = SUBMITTED_JOBS
		JOB_TARGETS = JOB_TARGETS
		BLOCKED_WRITES = BLOCKED_WRITES
		BLOCKED_PROVIDER_CALLS = BLOCKED_PROVIDER_CALLS

	return _GuardView()


class _NoopExecutor(BaseExecutor):
	"""Executor that accepts a job submission and runs nothing."""

	def _do_submit_job(self, job, run_times):
		SUBMITTED_JOBS.append(job.id)
		self._logger.info(
			"Test guard: suppressed execution of job %s", job.id
		)


@pytest.fixture(scope="session", autouse=True)
def _block_scheduler_job_execution():
	"""Stop the live scheduler from invoking real job functions."""
	from app import scheduler as scheduler_module

	scheduler = scheduler_module.scheduler

	# Layer 1: replace the default executor with one that discards jobs.
	executors = getattr(scheduler, "_executors", None)
	if isinstance(executors, dict):
		previous = executors.get("default")
		noop = _NoopExecutor()
		noop.start(scheduler, "default")
		executors["default"] = noop

		yield

		if previous is not None:
			executors["default"] = previous
		return

	# Layer 1 unavailable on this APScheduler version: fall back to the
	# recorders alone, which still prevent the real functions from running.
	yield


@pytest.fixture(scope="session", autouse=True)
def _record_scheduler_job_targets():
	"""Wrap real job targets so invoking one raises instead of writing."""
	from app import scheduler as scheduler_module

	originals = {}
	call_count = {name: 0 for name in JOB_TARGETS}

	def make_recorder(name, real):
		def recorder(*args, **kwargs):
			call_count[name] += 1
			EXECUTED_TARGETS.append(name)
			raise AssertionError(
				"test guard: real scheduled job %r must not execute during "
				"tests. It would run against the configured Supabase "
				"database." % name
			)

		recorder.__name__ = getattr(real, "__name__", name)
		recorder.__doc__ = getattr(real, "__doc__", None)
		return recorder

	for name in JOB_TARGETS:
		real = getattr(scheduler_module, name, None)
		if real is None:
			continue
		originals[name] = real
		setattr(scheduler_module, name, make_recorder(name, real))

	yield

	for name, real in originals.items():
		setattr(scheduler_module, name, real)

	if EXECUTED_TARGETS:
		raise AssertionError(
			"real scheduled jobs executed during the test run: %s"
			% sorted(set(EXECUTED_TARGETS))
		)


@pytest.fixture(autouse=True)
def _stop_scheduler_between_tests():
	"""Shut the scheduler down after each test.

	A scheduler left running keeps its own thread alive, so a job could
	fire during an unrelated test. Stopping it between tests keeps each
	test's scheduler lifetime inside that test.
	"""
	yield

	from app import scheduler as scheduler_module

	scheduler = getattr(scheduler_module, "scheduler", None)
	if scheduler is not None and scheduler.running:
		try:
			scheduler.shutdown(wait=False)
		except Exception:  # pragma: no cover - defensive
			pass


@pytest.fixture(scope="session", autouse=True)
def _block_production_supabase_writes():
	"""Refuse mutating Supabase operations for the whole test session.

	This is the backstop that makes a production write impossible from any
	test, including a test that calls `generate_india()` directly. Those
	job functions build a real `PerformanceService()` and snapshot a real
	portfolio, which wrote 6 rows into the production
	`portfolio_performance` table on 2026-10-02 even though the scheduler
	itself never fired.

	Reads are left alone so tests that inspect cached data still work.
	Tests that need writes mock the repository, not this client, so they
	are unaffected.
	"""
	from app.core.supabase import supabase

	original_table = supabase.table

	def guarded_table(name):
		table = original_table(name)

		for method in _MUTATIONS:
			def blocked(*args, _method=method, **kwargs):
				if _PROBE_DEPTH == 0:
					BLOCKED_WRITES.append((name, _method))
				raise AssertionError(
					"test guard: refusing to run %r on table %r. The test "
					"client points at the live Supabase project, so a write "
					"here would modify production data. Mock the repository "
					"in the test instead." % (_method, name)
				)

			setattr(table, method, blocked)

		return table

	supabase.table = guarded_table

	yield

	supabase.table = original_table

	if BLOCKED_WRITES:
		raise AssertionError(
			"tests attempted %d production Supabase write(s): %s"
			% (len(BLOCKED_WRITES), sorted(set(BLOCKED_WRITES)))
		)


@pytest.fixture(scope="session", autouse=True)
def _block_external_market_data_calls():
	"""Refuse real Yahoo calls for the whole test session.

	`PerformanceService` is contractually database-only, and the benchmark
	quote refresh is the one new path allowed to reach a provider. Both are
	supposed to be reached through mocks in tests, so any test that actually
	performs a network call is a test bug.

	This matters beyond correctness. Production market-data egress is capped at
	50 MB/day, and a test run that calls Yahoo spends that budget from the
	developer's machine rather than proving the production path is bounded.

	Only the outbound methods are blocked, so `YahooService` can still be
	constructed and its pure helpers still exercised.
	"""
	from app.services.yahoo_service import YahooService

	originals = {}

	def make_blocked(name):
		def blocked(*args, **kwargs):
			BLOCKED_PROVIDER_CALLS.append(name)
			raise AssertionError(
				"test guard: refusing to call YahooService.%s(). Mock the "
				"provider in the test instead; a real call spends the "
				"production market-data egress budget." % name
			)

		return blocked

	for name in _NETWORK_METHODS:
		originals[name] = getattr(YahooService, name, None)
		if originals[name] is not None:
			setattr(YahooService, name, make_blocked(name))

	yield

	for name, real in originals.items():
		if real is not None:
			setattr(YahooService, name, real)
