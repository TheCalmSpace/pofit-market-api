"""
Price validation and anomaly detection for the POFIT engine.

This module validates current prices against recent historical closes
and flags implausible discrepancies that may indicate data errors,
corporate actions, or upstream feed problems.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.models.quote import HistoricalResponse, QuoteResponse

logger = logging.getLogger(__name__)


@dataclass
class PriceAnomaly:
    """Represents a detected price anomaly."""
    current_price: Optional[float]
    previous_close: Optional[float]
    latest_historical_close: Optional[float]
    current_timestamp: Optional[str]
    historical_timestamp: Optional[str]
    discrepancy_pct: Optional[float]
    reason: str
    status: str  # VALID, REVIEW_REQUIRED, INVALID


@dataclass
class PriceValidationResult:
    """Result of price validation."""
    status: str  # VALID, REVIEW_REQUIRED, INVALID, INSUFFICIENT_DATA
    anomaly: Optional[PriceAnomaly] = None
    reasons: List[str] = field(default_factory=list)

    def is_actionable(self) -> bool:
        return self.status == "VALID"


class PriceValidator:
    """
    Validates quote prices against historical data.

    Detects:
    - Non-positive prices
    - NaN/Inf values
    - Major unexplained discrepancies with recent close
    """

    # Thresholds
    MAX_REVIEW_DISCREPANCY_PCT = 15.0  # >15% triggers review
    MAX_INVALID_DISCREPANCY_PCT = 50.0  # >50% without explanation = invalid

    @classmethod
    def validate_quote(
        cls,
        quote: QuoteResponse,
        history: Optional[HistoricalResponse] = None,
    ) -> PriceValidationResult:
        """
        Validate a quote against optional historical data.

        Parameters
        ----------
        quote : QuoteResponse
            Current quote data.
        history : HistoricalResponse, optional
            Recent price history for anomaly detection.

        Returns
        -------
        PriceValidationResult
        """
        reasons: List[str] = []

        # Basic structural checks
        price = cls._safe_float(quote.current_price)
        if price is None or price <= 0:
            return PriceValidationResult(
                status="INVALID",
                reasons=["current_price is missing, non-positive, or non-numeric"],
            )

        # Check for NaN/Inf
        if not math.isfinite(price):
            return PriceValidationResult(
                status="INVALID",
                reasons=["current_price is NaN or Infinity"],
            )

        # Currency sanity (optional, warn only)
        currency = getattr(quote, "currency", None)
        if currency and len(currency) != 3:
            reasons.append(f"Unusual currency code: {currency}")

        # If no history, we can only do basic validation
        if history is None or not history.prices:
            return PriceValidationResult(
                status="VALID",
                reasons=reasons + ["No historical data available for cross-check"],
            )

        latest_close, latest_adj_close, has_corporate_action = (
            cls._extract_latest_close_with_adjusted(history)
        )

        if latest_adj_close is not None and latest_adj_close > 0:
            reference_price = latest_adj_close
            reference_label = "adj_close"
            if has_corporate_action:
                reasons.append(
                    "Corporate action detected: using adj_close for discrepancy check"
                )
        elif latest_close is not None and latest_close > 0:
            reference_price = latest_close
            reference_label = "close"
            reasons.append(
                "No adj_close available; using raw close (corporate action unknown)"
            )
        else:
            return PriceValidationResult(
                status="VALID",
                reasons=reasons + ["Historical close unavailable for cross-check"],
            )

        discrepancy_pct = abs(price - reference_price) / reference_price * 100

        anomaly = PriceAnomaly(
            current_price=price,
            previous_close=cls._safe_float(getattr(quote, "previous_close", None)),
            latest_historical_close=reference_price,
            current_timestamp=None,
            historical_timestamp=history.prices[0].date.isoformat()
            if history.prices
            else None,
            discrepancy_pct=round(discrepancy_pct, 2),
            reason="",
            status="VALID",
        )

        if discrepancy_pct > cls.MAX_INVALID_DISCREPANCY_PCT:
            anomaly.status = "INVALID"
            if has_corporate_action:
                anomaly.reason = (
                    f"Price discrepancy {discrepancy_pct:.1f}% exceeds "
                    f"{cls.MAX_INVALID_DISCREPANCY_PCT}% threshold even after "
                    f"corporate-action adjustment ({reference_label})"
                )
            else:
                anomaly.reason = (
                    f"Price discrepancy {discrepancy_pct:.1f}% exceeds "
                    f"{cls.MAX_INVALID_DISCREPANCY_PCT}% threshold"
                )
            return PriceValidationResult(
                status="INVALID",
                anomaly=anomaly,
                reasons=reasons + [anomaly.reason],
            )

        if discrepancy_pct > cls.MAX_REVIEW_DISCREPANCY_PCT:
            anomaly.status = "REVIEW_REQUIRED"
            if has_corporate_action:
                anomaly.reason = (
                    f"Price discrepancy {discrepancy_pct:.1f}% exceeds "
                    f"{cls.MAX_REVIEW_DISCREPANCY_PCT}% review threshold; "
                    f"corporate action may explain but human review recommended"
                )
            else:
                anomaly.reason = (
                    f"Price discrepancy {discrepancy_pct:.1f}% exceeds "
                    f"{cls.MAX_REVIEW_DISCREPANCY_PCT}% review threshold"
                )
            return PriceValidationResult(
                status="REVIEW_REQUIRED",
                anomaly=anomaly,
                reasons=reasons + [anomaly.reason],
            )

        corporate_note = ""
        if has_corporate_action:
            corporate_note = (
                f"; corporate action adjusted ({reference_label})"
            )

        return PriceValidationResult(
            status="VALID",
            anomaly=anomaly,
            reasons=reasons + [
                f"Price within {discrepancy_pct:.1f}% of recent {reference_label}{corporate_note}"
            ],
        )

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
            if math.isfinite(v):
                return v
            return None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_latest_close_with_adjusted(
        history: HistoricalResponse,
    ) -> Tuple[Optional[float], Optional[float], bool]:
        """
        Extract the latest close and adjusted close from history.

        Returns
        -------
        (latest_close, latest_adj_close, has_corporate_action)

        has_corporate_action is True when adj_close exists and differs
        significantly from close, suggesting a stock split, bonus, or
        other corporate action.
        """
        latest_close = None
        latest_adj_close = None
        has_corporate_action = False

        for price in history.prices:
            close = PriceValidator._safe_float(price.close)
            adj_close = PriceValidator._safe_float(getattr(price, "adj_close", None))

            if close is not None and close > 0 and latest_close is None:
                latest_close = close

            if adj_close is not None and adj_close > 0 and latest_adj_close is None:
                latest_adj_close = adj_close

            if latest_close is not None and latest_adj_close is not None:
                break

        if (
            latest_close is not None
            and latest_adj_close is not None
            and latest_close > 0
        ):
            ratio = latest_adj_close / latest_close
            if ratio <= 0.5 or ratio >= 2.0:
                has_corporate_action = True

        return latest_close, latest_adj_close, has_corporate_action
