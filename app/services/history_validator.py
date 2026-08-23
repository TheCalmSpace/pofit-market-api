"""
Historical price/candle validation for the POFIT engine.

Validates OHLCV candles before they are used by the scoring engine.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

from app.models.quote import HistoricalResponse

logger = logging.getLogger(__name__)


@dataclass
class CandleValidationResult:
    """Result of validating a single candle."""
    date: str
    is_valid: bool
    issues: List[str] = field(default_factory=list)


@dataclass
class HistoryValidationResult:
    """Result of validating a full history dataset."""
    status: str  # VALID, INVALID, INSUFFICIENT_DATA
    valid_candles: int = 0
    invalid_candles: int = 0
    issues: List[str] = field(default_factory=list)
    details: List[CandleValidationResult] = field(default_factory=list)

    def is_usable(self) -> bool:
        return self.status == "VALID" and self.valid_candles > 0


class HistoryValidator:
    """
    Validates historical price data for integrity.

    Checks:
    - Valid timestamps
    - Positive prices
    - high >= low
    - high >= open/close
    - low <= open/close
    - Valid volume
    - No duplicate timestamps
    - No malformed candles
    """

    @classmethod
    def validate(cls, history: HistoricalResponse) -> HistoryValidationResult:
        """
        Validate a HistoricalResponse dataset.

        Parameters
        ----------
        history : HistoricalResponse
            Historical price data to validate.

        Returns
        -------
        HistoryValidationResult
        """
        if history is None or not history.prices:
            return HistoryValidationResult(
                status="INSUFFICIENT_DATA",
                issues=["No historical price data available"],
            )

        issues: List[str] = []
        details: List[CandleValidationResult] = []
        valid_count = 0
        invalid_count = 0
        seen_dates: set = set()

        for i, price in enumerate(history.prices):
            candle_issues = cls._validate_candle(price, seen_dates)
            is_valid = len(candle_issues) == 0
            detail = CandleValidationResult(
                date=price.date.isoformat() if price.date else f"index_{i}",
                is_valid=is_valid,
                issues=candle_issues,
            )
            details.append(detail)

            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1
                issues.extend(candle_issues)

        # Determine overall status
        total = len(history.prices)
        if valid_count == 0:
            status = "INVALID"
        elif invalid_count > 0:
            status = "INVALID"
        else:
            status = "VALID"

        if invalid_count > 0:
            logger.warning(
                "History validation: %d/%d candles invalid for %s. Issues: %s",
                invalid_count,
                total,
                history.symbol,
                issues[:5],
            )

        return HistoryValidationResult(
            status=status,
            valid_candles=valid_count,
            invalid_candles=invalid_count,
            issues=issues,
            details=details,
        )

    @staticmethod
    def _validate_candle(
        price: Any, seen_dates: set
    ) -> List[str]:
        issues: List[str] = []

        date_val = getattr(price, "date", None)
        if date_val is None:
            issues.append("Missing date")
        else:
            date_str = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
            if date_str in seen_dates:
                issues.append(f"Duplicate date: {date_str}")
            seen_dates.add(date_str)

        open_ = HistoryValidator._safe_float(getattr(price, "open", None))
        high = HistoryValidator._safe_float(getattr(price, "high", None))
        low = HistoryValidator._safe_float(getattr(price, "low", None))
        close = HistoryValidator._safe_float(getattr(price, "close", None))
        volume = getattr(price, "volume", None)

        # Price presence: at least one of open/high/low/close should exist
        if all(v is None for v in [open_, high, low, close]):
            issues.append("All price fields (OHLC) are null")
            return issues

        # Positive price checks
        for field_name, value in [("open", open_), ("high", high), ("low", low), ("close", close)]:
            if value is not None and value <= 0:
                issues.append(f"{field_name} is non-positive: {value}")

        # OHLC relationship checks
        if high is not None and low is not None and high < low:
            issues.append(f"high ({high}) < low ({low})")

        if high is not None and open_ is not None and high < open_:
            issues.append(f"high ({high}) < open ({open_})")
        if high is not None and close is not None and high < close:
            issues.append(f"high ({high}) < close ({close})")

        if low is not None and open_ is not None and low > open_:
            issues.append(f"low ({low}) > open ({open_})")
        if low is not None and close is not None and low > close:
            issues.append(f"low ({low}) > close ({close})")

        # Volume check (if present, must be non-negative)
        if volume is not None:
            try:
                vol_int = int(volume)
                if vol_int < 0:
                    issues.append(f"Volume is negative: {vol_int}")
            except (TypeError, ValueError):
                issues.append(f"Volume is not a valid integer: {volume}")

        return issues

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
            if v != v:  # NaN check
                return None
            return v
        except (TypeError, ValueError):
            return None
