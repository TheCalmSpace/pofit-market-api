"""
Unified data quality gate for the POFIT engine.

This module provides a single reusable entry point for validating
all data before it reaches the scoring engine or decision layer.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.models.financial_history import FinancialHistoryResponse
from app.models.financials import FinancialsResponse
from app.models.quote import HistoricalResponse, QuoteResponse

from app.services.data_quality import DataQualityAssessor
from app.services.financial_validator import FinancialValidator
from app.services.history_validator import HistoryValidator
from app.services.price_validator import PriceValidator

logger = logging.getLogger(__name__)


@dataclass
class DataQualityReport:
    """
    Comprehensive data quality report for a stock.

    This is the single source of truth for whether a stock's data
    is safe enough to score or act upon.
    """
    symbol: str
    overall_status: str = "INSUFFICIENT_DATA"
    quote_status: str = "insufficient"
    quote_reason: Optional[str] = None
    price_validation_status: str = "insufficient"
    price_anomaly: Optional[Dict[str, Any]] = None
    history_status: str = "insufficient"
    history_issues: List[str] = field(default_factory=list)
    financial_status: str = "insufficient"
    company_type: Optional[str] = None
    unavailable_metrics: List[str] = field(default_factory=list)
    score_status: str = "insufficient"
    score_reason: Optional[str] = None
    is_scoring_allowed: bool = False
    is_actionable: bool = False
    issues: List[str] = field(default_factory=list)
    data_as_of: Optional[str] = None
    calculated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "overall_status": self.overall_status,
            "quote_status": self.quote_status,
            "quote_reason": self.quote_reason,
            "price_validation_status": self.price_validation_status,
            "price_anomaly": self.price_anomaly,
            "history_status": self.history_status,
            "history_issues": self.history_issues,
            "financial_status": self.financial_status,
            "company_type": self.company_type,
            "unavailable_metrics": self.unavailable_metrics,
            "score_status": self.score_status,
            "score_reason": self.score_reason,
            "is_scoring_allowed": self.is_scoring_allowed,
            "is_actionable": self.is_actionable,
            "issues": self.issues,
            "data_as_of": self.data_as_of,
            "calculated_at": self.calculated_at,
        }


class DataQualityGate:
    """
    Single reusable data quality gate for the POFIT engine.

    Flow:
    Market Data → Validation → Freshness → Completeness → Data Quality Gate
    → SAFE (score/decide) or UNSAFE (no actionable score)

    This gate should be called before any scoring or decision logic.
    """

    @classmethod
    def assess(
        cls,
        symbol: str,
        quote: Optional[Any],
        history: Optional[Any],
        financials: Optional[Any],
        financial_history: Optional[Any],
        cached: Optional[Dict[str, Any]] = None,
    ) -> DataQualityReport:
        """
        Perform comprehensive data quality assessment.

        Parameters
        ----------
        symbol : str
            Stock symbol.
        quote : QuoteResponse or dict, optional
            Current quote.
        history : HistoricalResponse or dict, optional
            Recent price history.
        financials : FinancialsResponse or dict, optional
            Latest financial snapshot.
        financial_history : FinancialHistoryResponse or dict, optional
            Historical financial statements.
        cached : dict, optional
            Raw cached data from stock_data table.

        Returns
        -------
        DataQualityReport
        """
        report = DataQualityReport(symbol=symbol)
        issues: List[str] = []

        # Normalize dicts to model objects where needed
        quote_model = cls._to_quote_model(quote)
        history_model = cls._to_history_model(history)
        financials_model = cls._to_financials_model(financials)
        financial_history_model = cls._to_financial_history_model(financial_history)

        # 1. Quote validation (cached + live)
        quote_quality = DataQualityAssessor.assess_quote(cached)
        report.quote_status = quote_quality["status"]
        report.quote_reason = quote_quality.get("reason")

        if quote_model is not None:
            price_result = PriceValidator.validate_quote(quote_model, history_model)
            report.price_validation_status = price_result.status
            if price_result.anomaly:
                report.price_anomaly = {
                    "current_price": price_result.anomaly.current_price,
                    "previous_close": price_result.anomaly.previous_close,
                    "latest_historical_close": price_result.anomaly.latest_historical_close,
                    "discrepancy_pct": price_result.anomaly.discrepancy_pct,
                    "reason": price_result.anomaly.reason,
                    "status": price_result.anomaly.status,
                }
            issues.extend(price_result.reasons)

        # 2. History validation
        if history_model is not None:
            history_result = HistoryValidator.validate(history_model)
            report.history_status = history_result.status
            report.history_issues = history_result.issues
            if not history_result.is_usable():
                issues.append(f"History invalid: {', '.join(history_result.issues[:3])}")
        else:
            report.history_status = "insufficient"
            report.history_issues = ["No historical data provided"]

        # 3. Financial validation
        if financials_model is not None:
            fin_result = FinancialValidator.validate(financials_model, financial_history_model)
            report.financial_status = fin_result.status
            report.company_type = fin_result.company_type
            report.unavailable_metrics = fin_result.unavailable_metrics
            if fin_result.issues:
                issues.extend(fin_result.issues)
        else:
            report.financial_status = "insufficient"
            report.history_issues = report.history_issues or []
            report.history_issues.append("No financial data provided")

        # 4. Score freshness (if cached)
        if cached:
            score_quality = DataQualityAssessor.assess_score(cached)
            report.score_status = score_quality["status"]
            report.score_reason = score_quality.get("reason")
            report.data_as_of = cached.get("updated_at")
        else:
            report.score_status = "insufficient"
            report.score_reason = "No cached data available"

        report.calculated_at = datetime.utcnow().isoformat() + "Z"
        report.issues = issues

        # 5. Determine overall status and scoring eligibility
        cls._determine_scoping(report)

        return report

    @staticmethod
    def _to_quote_model(quote: Any) -> Optional[QuoteResponse]:
        if quote is None:
            return None
        if isinstance(quote, QuoteResponse):
            return quote
        if isinstance(quote, dict):
            try:
                return QuoteResponse(**quote)
            except Exception:
                return None
        return None

    @staticmethod
    def _to_history_model(history: Any) -> Optional[HistoricalResponse]:
        if history is None:
            return None
        if isinstance(history, HistoricalResponse):
            return history
        if isinstance(history, dict):
            try:
                return HistoricalResponse(**history)
            except Exception:
                return None
        return None

    @staticmethod
    def _to_financials_model(financials: Any) -> Optional[FinancialsResponse]:
        if financials is None:
            return None
        if isinstance(financials, FinancialsResponse):
            return financials
        if isinstance(financials, dict):
            try:
                return FinancialsResponse(**financials)
            except Exception:
                return None
        return None

    @staticmethod
    def _to_financial_history_model(history: Any) -> Optional[FinancialHistoryResponse]:
        if history is None:
            return None
        if isinstance(history, FinancialHistoryResponse):
            return history
        if isinstance(history, dict):
            try:
                return FinancialHistoryResponse(**history)
            except Exception:
                return None
        return None

    @staticmethod
    def _determine_scoping(report: DataQualityReport) -> None:
        """
        Set overall_status, is_scoring_allowed, and is_actionable
        based on component statuses.
        """
        blocking_statuses = {"invalid", "insufficient", "UPSTREAM_ERROR"}
        review_statuses = {"stale", "partial", "REVIEW_REQUIRED"}

        quote_ok = report.quote_status not in blocking_statuses
        price_ok = report.price_validation_status not in ("INVALID", "insufficient")
        
        # Price history is only a blocker if it was explicitly validated and failed.
        # Missing price history alone should not block scoring, because the scoring
        # engine consumes financial history, not price candles.
        if report.history_status == "insufficient" and report.price_validation_status != "INVALID":
            history_ok = True
        else:
            history_ok = report.history_status not in blocking_statuses
        
        financial_ok = report.financial_status not in blocking_statuses

        if not quote_ok or not price_ok or not financial_ok:
            report.overall_status = "INSUFFICIENT_DATA"
            report.is_scoring_allowed = False
            report.is_actionable = False
            return

        needs_review = (
            report.price_validation_status == "REVIEW_REQUIRED"
            or report.score_status in review_statuses
            or report.history_status in review_statuses
            or report.financial_status in review_statuses
        )

        if needs_review:
            report.overall_status = "REVIEW_REQUIRED"
            report.is_scoring_allowed = False
            report.is_actionable = False
            return

        report.overall_status = "SAFE"
        report.is_scoring_allowed = True
        report.is_actionable = True
