"""
Financial data validation and company-type awareness for the POFIT engine.

Validates financial data for completeness, consistency, and freshness.
Identifies company types where certain metrics are not applicable.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.models.financials import FinancialsResponse
from app.models.financial_history import FinancialHistoryResponse

logger = logging.getLogger(__name__)


@dataclass
class FinancialValidationResult:
    """Result of financial data validation."""
    status: str  # VALID, PARTIAL, INVALID, INSUFFICIENT_DATA
    company_type: Optional[str] = None
    missing_required_fields: List[str] = field(default_factory=list)
    unavailable_metrics: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    data_as_of: Optional[str] = None


class FinancialValidator:
    """
    Validates financial data and identifies company types.

    Company types affect which metrics are applicable:
    - BANKS: traditional debt/equity, EBITDA may not apply
    - NBFCs: similar to banks
    - INSURANCE: reserve-based metrics differ
    - REITs: property-based metrics, traditional manufacturing metrics don't apply
    - InvITs: infrastructure fund metrics differ
    - OTHER: standard corporate assumptions
    """

    COMPANY_TYPE_KEYWORDS: Dict[str, Set[str]] = {
        "BANK": {"bank", "banking", "financial services"},
        "NBFC": {"nbfc", "non-banking financial", "nb financial"},
        "INSURANCE": {"insurance", "insurer", "life insurance", "general insurance"},
        "REIT": {"reit", "real estate investment trust", "property"},
        "INVIT": {"invit", "infrastructure investment trust"},
    }

    # Metrics not applicable by company type
    INAPPLICABLE_METRICS: Dict[str, Set[str]] = {
        "BANK": {"debt_to_equity", "current_ratio", "quick_ratio", "ebitda"},
        "NBFC": {"debt_to_equity", "current_ratio", "quick_ratio", "ebitda"},
        "INSURANCE": {"debt_to_equity", "current_ratio", "quick_ratio", "ebitda"},
        "REIT": {"debt_to_equity", "current_ratio", "quick_ratio", "ebitda"},
        "INVIT": {"debt_to_equity", "current_ratio", "quick_ratio", "ebitda"},
    }

    @classmethod
    def validate(
        cls,
        financials: Any,
        history: Any = None,
    ) -> FinancialValidationResult:
        """
        Validate financial data for scoring readiness.

        Parameters
        ----------
        financials : FinancialsResponse or dict
            Latest financial snapshot.
        history : FinancialHistoryResponse or dict, optional
            Historical financial statements.

        Returns
        -------
        FinancialValidationResult
        """
        if financials is None:
            return FinancialValidationResult(
                status="INSUFFICIENT_DATA",
                issues=["FinancialsResponse is None"],
            )

        if isinstance(financials, dict):
            try:
                financials = FinancialsResponse(**financials)
            except Exception as exc:
                return FinancialValidationResult(
                    status="INVALID",
                    issues=[f"Failed to parse financials: {exc}"],
                )

        if financials.data_status == "unavailable":
            return FinancialValidationResult(
                status="INSUFFICIENT_DATA",
                issues=[financials.unavailable_reason or "Financial data unavailable"],
            )

        if history is not None and isinstance(history, dict):
            try:
                history = FinancialHistoryResponse(**history)
            except Exception:
                history = None

        # Identify company type
        company_type = cls._identify_company_type(financials)
        unavailable_metrics = cls.INAPPLICABLE_METRICS.get(company_type, set())

        # Check required fields for scoring
        missing = cls._check_required_fields(financials, company_type)
        issues: List[str] = []

        if missing:
            issues.append(f"Missing required fields: {', '.join(missing)}")

        # Validate financial history if provided
        if history is not None:
            history_issues = cls._validate_history(history, company_type)
            issues.extend(history_issues)

        # Determine status
        if not missing and not issues:
            status = "VALID"
        elif missing:
            status = "PARTIAL"
        else:
            status = "VALID"

        return FinancialValidationResult(
            status=status,
            company_type=company_type,
            missing_required_fields=missing,
            unavailable_metrics=list(unavailable_metrics),
            issues=issues,
            data_as_of=financials.data_status if hasattr(financials, 'data_status') else None,
        )

    @staticmethod
    def _identify_company_type(financials: FinancialsResponse) -> Optional[str]:
        """Identify company type from sector/industry/name."""
        text_fields = [
            (financials.sector or "").lower(),
            (financials.industry or "").lower(),
            (financials.company_name or "").lower(),
        ]
        combined = " ".join(text_fields)

        for company_type, keywords in FinancialValidator.COMPANY_TYPE_KEYWORDS.items():
            if any(keyword in combined for keyword in keywords):
                return company_type

        return "OTHER"

    @staticmethod
    def _check_required_fields(
        financials: FinancialsResponse, company_type: Optional[str]
    ) -> List[str]:
        """Check for missing required financial fields."""
        # Core fields always required for scoring
        required = [
            "revenue_ttm",
            "net_income",
            "total_equity",
            "market_cap",
            "trailing_pe",
        ]

        missing = []
        for field_name in required:
            value = getattr(financials, field_name, None)
            if value is None:
                missing.append(field_name)

        # For non-financial companies, also check debt and assets
        if company_type not in ("BANK", "NBFC", "INSURANCE", "REIT", "INVIT"):
            extra_fields = ["total_debt", "total_assets", "current_ratio"]
            for field_name in extra_fields:
                value = getattr(financials, field_name, None)
                if value is None:
                    missing.append(field_name)

        return missing

    @staticmethod
    def _validate_history(
        history: FinancialHistoryResponse, company_type: Optional[str]
    ) -> List[str]:
        """Validate financial history data."""
        issues = []

        if not history.annual:
            issues.append("No annual financial history available")
            return issues

        # Check for sufficient history
        if len(history.annual) < 2:
            issues.append("Insufficient financial history (need at least 2 years)")

        # Check for valid years
        current_year = datetime.now().year
        years = [item.year for item in history.annual if item.year is not None]
        if years:
            oldest_year = min(years)
            if oldest_year < current_year - 10:
                issues.append(f"Financial history may be stale (oldest year: {oldest_year})")

        # Check for all-None entries
        null_entries = sum(
            1 for item in history.annual
            if item.revenue is None and item.net_income is None
        )
        if null_entries == len(history.annual):
            issues.append("All annual entries have null revenue and net income")

        return issues
