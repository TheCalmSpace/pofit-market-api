import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from curl_cffi.requests import Response, Session
from requests import Session as RequestsSession

from app.services.yahoo_service import YahooService


DAILY_BUDGET_DB = (
    Path(tempfile.gettempdir()) / "pofit-egress-budget.sqlite3"
)

# Project-wide daily egress ceiling: 100 MB absolute maximum.
# Shared daily operational budget: 80 MB with 20% safety margin below ceiling.
# This is shared across ALL external call paths (Yahoo, NSE, etc.).
# NOTE: The ledger lives in the host's temporary directory. The limit applies
# only to traffic coordinated through that ledger on that host. It does NOT
# guarantee a global 100 MB ceiling across independent hosts or replicas.
PROJECT_DAILY_EGRESS_BYTES = 80_000_000
PROJECT_ABSOLUTE_EGRESS_CEILING = 100_000_000
PROJECT_DAILY_REQUESTS = 200

# Per-run caps for the shared sessions (conservative to leave room for concurrent users).
SHARED_MAX_RESPONSE_BYTES = 5_000_000
SHARED_MAX_REQUESTS = 50

# NSE-specific caps (smaller responses, fewer requests per run).
NSE_MAX_RESPONSE_BYTES = 2_000_000
NSE_MAX_REQUESTS = 30


class EgressLimitError(RuntimeError):
    """Raised before a request or response exceeds the project budget."""


class EgressBudget:
    """Shared daily egress budget backed by SQLite.

    All bounded sessions (Yahoo, NSE) should use the same EgressBudget instance
    to enforce a single project-wide daily ceiling.
    """

    def __init__(
        self,
        db_path: Path,
        max_daily_bytes: int,
        max_daily_requests: int,
        request_overhead_bytes: int = 16_384,
    ) -> None:
        self.db_path = db_path
        self.max_daily_bytes = max_daily_bytes
        self.max_daily_requests = max_daily_requests
        self.request_overhead_bytes = request_overhead_bytes
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS egress_daily ("
                "day TEXT PRIMARY KEY, used_bytes INTEGER NOT NULL, "
                "request_count INTEGER NOT NULL)"
            )

    def reserve(
        self,
        run_remaining: int,
        run_requests_remaining: int,
    ) -> tuple[int, int]:
        """Atomically reserve body capacity and request overhead for today.

        Returns (body_budget, requests_reserved) where requests_reserved is 1.
        Raises EgressLimitError if budget exhausted.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            with sqlite3.connect(
                str(self.db_path),
                timeout=10,
                isolation_level=None,
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM egress_daily WHERE day < date(?, '-7 days')",
                    (today,),
                )
                row = connection.execute(
                    "SELECT used_bytes, request_count FROM egress_daily WHERE day = ?",
                    (today,),
                ).fetchone()
                used_bytes, request_count = row or (0, 0)
                remaining_bytes = self.max_daily_bytes - used_bytes
                remaining_requests = self.max_daily_requests - request_count
                body_budget = min(
                    run_remaining,
                    remaining_bytes - self.request_overhead_bytes,
                )
                req_budget = min(
                    run_requests_remaining,
                    remaining_requests,
                )
                if req_budget <= 0 or body_budget <= 0:
                    connection.rollback()
                    raise EgressLimitError(
                        "Daily egress budget exhausted; refusing request before network access."
                    )
                connection.execute(
                    "INSERT INTO egress_daily "
                    "(day, used_bytes, request_count) VALUES (?, ?, ?) "
                    "ON CONFLICT(day) DO UPDATE SET "
                    "used_bytes = excluded.used_bytes, "
                    "request_count = excluded.request_count",
                    (
                        today,
                        used_bytes + body_budget + self.request_overhead_bytes,
                        request_count + 1,
                    ),
                )
                connection.commit()
                return body_budget, 1
        except EgressLimitError:
            raise
        except sqlite3.Error as exc:
            raise EgressLimitError(
                "Unable to verify the persistent daily egress budget; refusing network access."
            ) from exc

    def release(self, reserved_body_bytes: int, actual_body_bytes: int) -> None:
        unused = reserved_body_bytes - actual_body_bytes
        if unused <= 0:
            return
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            with sqlite3.connect(
                str(self.db_path),
                timeout=10,
                isolation_level=None,
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE egress_daily SET used_bytes = used_bytes - ? WHERE day = ? AND used_bytes >= ?",
                    (unused, today, unused),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise EgressLimitError(
                "Unable to reconcile the persistent daily egress budget; stopping further acquisition."
            ) from exc


_SHARED_BUDGET: Optional["EgressBudget"] = None


def get_shared_budget() -> "EgressBudget":
    """Return the process-wide shared EgressBudget."""
    global _SHARED_BUDGET
    if _SHARED_BUDGET is None:
        _SHARED_BUDGET = EgressBudget(
            db_path=DAILY_BUDGET_DB,
            max_daily_bytes=PROJECT_DAILY_EGRESS_BYTES,
            max_daily_requests=PROJECT_DAILY_REQUESTS,
        )
    return _SHARED_BUDGET


class BoundedSessionMixin:
    """Mixin adding per-run and persistent daily egress limits to a session."""

    def __init__(
        self,
        budget: "EgressBudget",
        max_response_bytes: int,
        max_requests: int,
        request_overhead_bytes: int = 16_384,
    ) -> None:
        self._budget = budget
        self.max_response_bytes = max_response_bytes
        self.max_requests = max_requests
        self.request_overhead_bytes = request_overhead_bytes
        self.response_bytes = 0
        self.request_count = 0

    def _check_run_limits(self) -> None:
        if self.request_count >= self.max_requests:
            raise EgressLimitError("HTTP request limit reached for this run.")
        if self.max_response_bytes - self.response_bytes <= 0:
            raise EgressLimitError("Response-byte limit reached for this run.")

    def _reserve_budget(self) -> int:
        run_remaining = self.max_response_bytes - self.response_bytes
        run_req_remaining = self.max_requests - self.request_count
        body_budget, _ = self._budget.reserve(run_remaining, run_req_remaining)
        return body_budget

    def _release_budget(self, reserved: int, actual: int) -> None:
        self._budget.release(reserved, actual)

    @staticmethod
    def _content_length(headers: Dict[str, Any]) -> Optional[int]:
        raw = headers.get("content-length")
        if raw is None:
            return None
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return None
        return val if val >= 0 else None


class BoundedYahooSession(BoundedSessionMixin, Session):
    """curl_cffi Session with shared project-wide egress limits.

    Uses streaming to enforce byte limits during download. This can break
    libraries like yfinance that expect non-streaming responses.
    """

    def __init__(
        self,
        max_response_bytes: int = SHARED_MAX_RESPONSE_BYTES,
        max_requests: int = SHARED_MAX_REQUESTS,
        budget: Optional["EgressBudget"] = None,
    ) -> None:
        Session.__init__(self, impersonate="chrome")
        BoundedSessionMixin.__init__(
            self,
            budget=budget or get_shared_budget(),
            max_response_bytes=max_response_bytes,
            max_requests=max_requests,
        )

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self._check_run_limits()
        body_budget = self._reserve_budget()
        self.request_count += 1
        kwargs["stream"] = True
        response = super().request(method, url, **kwargs)

        content_length = self._content_length(response.headers)
        stream_chunk_size = min(64 * 1024, body_budget)
        stream_cushion = (
            min(stream_chunk_size, body_budget // 2)
            if body_budget > stream_chunk_size
            else 0
        )
        body_limit = body_budget - stream_cushion
        if content_length is not None and content_length > body_limit:
            response.close()
            self._release_budget(body_budget, 0)
            raise EgressLimitError(
                "Response declares more bytes than the reserved daily and per-run budget."
            )

        chunks = []
        body_bytes = 0
        try:
            for chunk in response.iter_content(chunk_size=stream_chunk_size):
                if not chunk:
                    continue
                if body_bytes + len(chunk) > body_limit:
                    self.response_bytes += len(chunk)
                    response.close()
                    raise EgressLimitError("Response exceeded its reserved egress budget.")
                body_bytes += len(chunk)
                self.response_bytes += len(chunk)
                chunks.append(chunk)
                if body_bytes == body_limit and content_length is None:
                    response.close()
                    raise EgressLimitError(
                        "Response reached its reserved egress budget without a declared content length."
                    )
        except Exception:
            response.close()
            raise

        response.close()
        response.content = b"".join(chunks)
        response._text = ""
        self._release_budget(body_budget, body_bytes)
        return response


class BoundedYahooSessionCompat(BoundedSessionMixin, Session):
    """curl_cffi Session with shared project-wide egress limits (compat mode).

    Does NOT use streaming, so it works with yfinance and other libraries
    that expect standard response objects. Byte limits are enforced after
    the full response is received rather than during streaming.
    """

    def __init__(
        self,
        max_response_bytes: int = SHARED_MAX_RESPONSE_BYTES,
        max_requests: int = SHARED_MAX_REQUESTS,
        budget: Optional["EgressBudget"] = None,
    ) -> None:
        Session.__init__(self, impersonate="chrome")
        BoundedSessionMixin.__init__(
            self,
            budget=budget or get_shared_budget(),
            max_response_bytes=max_response_bytes,
            max_requests=max_requests,
        )

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self._check_run_limits()
        body_budget = self._reserve_budget()
        self.request_count += 1
        # Do NOT use streaming - compatibility with yfinance
        kwargs["stream"] = False
        response = super().request(method, url, **kwargs)

        # Enforce byte limit after response is received
        body_bytes = len(response.content) if response.content else 0
        self.response_bytes += body_bytes
        if body_bytes > body_budget:
            self._release_budget(body_budget, body_bytes)
            response.close()
            raise EgressLimitError("Response exceeded its reserved egress budget.")

        self._release_budget(body_budget, body_bytes)
        return response


class BoundedNSESession(BoundedSessionMixin, RequestsSession):
    """requests Session with shared project-wide egress limits."""

    def __init__(
        self,
        max_response_bytes: int = NSE_MAX_RESPONSE_BYTES,
        max_requests: int = NSE_MAX_REQUESTS,
        budget: Optional["EgressBudget"] = None,
    ) -> None:
        RequestsSession.__init__(self)
        BoundedSessionMixin.__init__(
            self,
            budget=budget or get_shared_budget(),
            max_response_bytes=max_response_bytes,
            max_requests=max_requests,
        )

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self._check_run_limits()
        body_budget = self._reserve_budget()
        self.request_count += 1
        kwargs.setdefault("stream", True)
        response = super().request(method, url, **kwargs)

        content_length = self._content_length(response.headers)
        stream_chunk_size = min(64 * 1024, body_budget)
        stream_cushion = (
            min(stream_chunk_size, body_budget // 2)
            if body_budget > stream_chunk_size
            else 0
        )
        body_limit = body_budget - stream_cushion
        if content_length is not None and content_length > body_limit:
            response.close()
            self._release_budget(body_budget, 0)
            raise EgressLimitError(
                "Response declares more bytes than the reserved daily and per-run budget."
            )

        body_bytes = 0
        chunks = []
        try:
            for chunk in response.iter_content(chunk_size=stream_chunk_size):
                if not chunk:
                    continue
                if body_bytes + len(chunk) > body_limit:
                    self.response_bytes += len(chunk)
                    response.close()
                    raise EgressLimitError("Response exceeded its reserved egress budget.")
                body_bytes += len(chunk)
                self.response_bytes += len(chunk)
                chunks.append(chunk)
                if body_bytes == body_limit and content_length is None:
                    response.close()
                    raise EgressLimitError(
                        "Response reached its reserved egress budget without a declared content length."
                    )
        except Exception:
            response.close()
            raise

        response.close()
        response.content = b"".join(chunks)
        self._release_budget(body_budget, body_bytes)
        return response


_shared_yahoo_session: Optional[BoundedYahooSession] = None


def get_shared_yahoo_session() -> BoundedYahooSession:
    """Return the process-wide shared BoundedYahooSession."""
    global _shared_yahoo_session
    if _shared_yahoo_session is None:
        _shared_yahoo_session = BoundedYahooSession()
    return _shared_yahoo_session


def create_yahoo_service() -> YahooService:
    """Create a YahooService that uses the shared egress-bounded session."""
    return YahooService(session=get_shared_yahoo_session())


def create_bounded_nse_session() -> BoundedNSESession:
    """Create a BoundedNSESession that uses the shared egress budget."""
    return BoundedNSESession()