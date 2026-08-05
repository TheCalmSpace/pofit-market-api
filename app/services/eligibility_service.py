from app.models.eligibility import EligibilityResponse
from app.models.financials import FinancialsResponse
from app.models.quote import QuoteResponse


class EligibilityService:
    """
    Builds the eligibility snapshot used by the POFIT Alpha engine.

    This service only extracts and normalizes the raw values required
    to determine whether a company is eligible for Alpha.

    It performs NO filtering and NO scoring.
    """

    @staticmethod
    def build(
        quote: QuoteResponse,
        financials: FinancialsResponse,
    ) -> EligibilityResponse:

        average_daily_value_90d = None

        average_volume = getattr(
            quote,
            "average_volume_90d",
            None,
        )

        if (
            average_volume is not None
            and quote.current_price is not None
        ):
            average_daily_value_90d = (
                average_volume
                * quote.current_price
            )

        market_cap = (
            financials.market_cap
            if financials.market_cap is not None
            else quote.market_cap
        )

        financials_complete = all(
            value is not None
            for value in (
                market_cap,
                financials.revenue_ttm,
                financials.net_income,
                financials.total_equity,
            )
        )

        return EligibilityResponse(
            market_cap=market_cap,
            average_daily_value_90d=average_daily_value_90d,
            revenue_ttm=financials.revenue_ttm,
            net_income_ttm=financials.net_income,
            shareholders_equity=financials.total_equity,
            financials_complete=financials_complete,
        )