from typing import Any, Dict, Tuple

from app.models.eligibility import EligibilityResponse


class AlphaFilterService:
    """
    Determines whether a stock is eligible for the POFIT Alpha Portfolio.

    Accepts either:
    - EligibilityResponse
    - dict (from cached eligibility_json)
    """

    INDIA_MARKET_CAP = 1_000 * 10_000_000      # ₹1,000 Cr
    USA_MARKET_CAP = 500_000_000               # $500M

    INDIA_ADTV = 5 * 10_000_000                # ₹5 Cr/day
    USA_ADTV = 5_000_000                       # $5M/day

    def is_eligible(
        self,
        country: str,
        eligibility: EligibilityResponse | Dict[str, Any],
    ) -> Tuple[bool, str]:

        if isinstance(eligibility, dict):
            eligibility = EligibilityResponse(**eligibility)

        country = (country or "").upper()

        if country in ("IN", "INDIA"):
            market_cap_limit = self.INDIA_MARKET_CAP
            liquidity_limit = self.INDIA_ADTV
        else:
            market_cap_limit = self.USA_MARKET_CAP
            liquidity_limit = self.USA_ADTV

        if not eligibility.financials_complete:
            return False, "Financial statements incomplete"

        if (
            eligibility.market_cap is None
            or eligibility.market_cap < market_cap_limit
        ):
            return False, "Market cap below threshold"

        if (
            eligibility.average_daily_value_90d is None
            or eligibility.average_daily_value_90d < liquidity_limit
        ):
            return False, "Liquidity below threshold"

        if (
            eligibility.revenue_ttm is None
            or eligibility.revenue_ttm <= 0
        ):
            return False, "Negative revenue"

        if (
            eligibility.net_income_ttm is None
            or eligibility.net_income_ttm <= 0
        ):
            return False, "Negative net income"

        if (
            eligibility.shareholders_equity is None
            or eligibility.shareholders_equity <= 0
        ):
            return False, "Negative shareholders equity"

        return True, "Eligible"