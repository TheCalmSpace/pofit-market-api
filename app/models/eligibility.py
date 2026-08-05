from typing import Optional

from pydantic import BaseModel


class EligibilityResponse(BaseModel):
    """
    Normalized eligibility snapshot used by the Alpha engine.

    This model stores only the raw values required to determine
    whether a company is eligible for the POFIT Alpha Portfolio.

    It does NOT perform any calculations or scoring.
    """

    # Company size
    market_cap: Optional[float] = None

    # Liquidity
    average_daily_value_90d: Optional[float] = None

    # Financial health
    revenue_ttm: Optional[float] = None
    net_income_ttm: Optional[float] = None
    shareholders_equity: Optional[float] = None

    # Data quality
    financials_complete: bool = False