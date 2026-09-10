import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from app.core.supabase import supabase as main_supabase
from app.services.data_quality import DataQualityAssessor
from app.services.score_service import ScoreService

logger = logging.getLogger(__name__)

PERSONAL_SUPABASE_URL = os.getenv("PERSONAL_SUPABASE_URL")
PERSONAL_SUPABASE_SERVICE_ROLE_KEY = os.getenv("PERSONAL_SUPABASE_SERVICE_ROLE_KEY")


@dataclass
class ScoreSyncReport:
    total_main_scores: int = 0
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    sync_duration_seconds: float = 0.0
    final_status: str = "pending"
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Score sync: main={self.total_main_scores} synced={self.synced} "
            f"skipped={self.skipped} failed={self.failed} "
            f"duration={self.sync_duration_seconds:.1f}s status={self.final_status}"
        )


class ScoreSyncService:
    """Synchronize authoritative Main Pofit scores into Pofit Personal."""

    def __init__(self) -> None:
        if not PERSONAL_SUPABASE_URL or not PERSONAL_SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError(
                "PERSONAL_SUPABASE_URL and PERSONAL_SUPABASE_SERVICE_ROLE_KEY must be set"
            )
        self.personal_supabase: Client = create_client(
            PERSONAL_SUPABASE_URL,
            PERSONAL_SUPABASE_SERVICE_ROLE_KEY,
        )
        self.main_supabase: Client = main_supabase

    def _fetch_main_scores(self) -> List[Dict[str, Any]]:
        result = (
            self.main_supabase.table("stock_data")
            .select("symbol,score_json,updated_at,quote_json,metrics_json")
            .execute()
        )
        return result.data or []

    def _fetch_personal_stocks(self) -> Dict[str, Dict[str, Any]]:
        result = (
            self.personal_supabase.table("stocks")
            .select("id,symbol,exchange")
            .execute()
        )
        by_key: Dict[str, Dict[str, Any]] = {}
        for row in result.data or []:
            symbol = str(row.get("symbol") or "").upper()
            exchange = str(row.get("exchange") or "").upper()
            if symbol:
                by_key[f"{symbol}|{exchange}"] = row
                by_key.setdefault(symbol, row)
        return by_key

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[str]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    @classmethod
    def _build_payload(
        cls,
        cached: Dict[str, Any],
        personal_stock: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        score = cached.get("score_json")
        if not isinstance(score, dict):
            return None

        required_fields = (
            "overall_score",
            "grade",
            "growth_score",
            "quality_score",
            "financial_strength_score",
            "valuation_score",
        )
        if any(score.get(field) is None for field in required_fields):
            return None

        score_quality_status = score.get("data_quality_status")
        if score_quality_status in {"insufficient", "invalid"}:
            return None

        calculated_at = cls._parse_timestamp(
            score.get("calculated_at") or cached.get("updated_at")
        )
        if calculated_at is None:
            return None

        quality = DataQualityAssessor.assess_score(cached)
        quality_status = quality.get("status")
        if quality_status in {"insufficient", "invalid"}:
            return None
        if quality_status == "stale":
            data_quality_status = "stale"
        else:
            data_quality_status = score_quality_status or "fresh"

        data_as_of = cls._parse_timestamp(
            score.get("data_as_of") or quality.get("data_as_of") or cached.get("updated_at")
        )

        return {
            "stock_id": personal_stock["id"],
            "calculated_at": calculated_at,
            "total_score": score["overall_score"],
            "grade": score["grade"],
            "model_version": score.get("model_version") or ScoreService.MODEL_VERSION,
            "data_as_of": data_as_of,
            "data_quality_status": data_quality_status,
            "growth_score": score["growth_score"],
            "quality_score": score["quality_score"],
            "valuation_score": score["valuation_score"],
            "financial_strength_score": score["financial_strength_score"],
            "momentum_score": score.get("momentum_score"),
            "risk_level": score.get("risk_level"),
        }

    def run(self) -> ScoreSyncReport:
        start = time.time()
        report = ScoreSyncReport()
        logger.info("Starting Main Pofit -> Pofit Personal score sync")

        try:
            main_scores = self._fetch_main_scores()
            personal_stocks = self._fetch_personal_stocks()
        except Exception as exc:
            report.final_status = "FAILED"
            report.failed = 1
            report.errors.append(str(exc))
            report.sync_duration_seconds = time.time() - start
            logger.error("Failed to prepare score sync: %s", exc)
            return report

        report.total_main_scores = len(main_scores)
        for cached in main_scores:
            symbol = str(cached.get("symbol") or "").upper()
            personal_stock = personal_stocks.get(symbol)
            if personal_stock is None:
                report.skipped += 1
                continue

            payload = self._build_payload(cached, personal_stock)
            if payload is None:
                report.skipped += 1
                continue

            try:
                self.personal_supabase.table("pofit_scores").upsert(
                    payload,
                    on_conflict="stock_id,calculated_at",
                ).execute()
                report.synced += 1
            except Exception as exc:
                report.failed += 1
                report.errors.append(f"{symbol}: {exc}")
                logger.error("Failed to sync score for %s: %s", symbol, exc)

        report.sync_duration_seconds = time.time() - start
        if report.failed and not report.synced:
            report.final_status = "FAILED"
        elif report.failed:
            report.final_status = "PARTIAL_SUCCESS"
        else:
            report.final_status = "SUCCESS"

        logger.info(report.summary())
        return report
