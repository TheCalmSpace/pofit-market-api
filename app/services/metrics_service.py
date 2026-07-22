"""
Metrics Service for POFIT.

This service converts normalized financial statements into
investment metrics that are later consumed by the POFIT scoring engine.

Responsibilities
----------------
- Growth Metrics
- Quality Metrics
- Financial Strength Metrics
- Valuation Metrics

This service NEVER talks directly to Yahoo Finance.

It only consumes normalized models returned by the Financial History
and Financials services.
"""

from app.models.financial_history import FinancialHistoryResponse
from app.models.financials import FinancialsResponse
from app.models.metrics import (
    FinancialStrengthMetrics,
    GrowthMetrics,
    MetricsResponse,
    QualityMetrics,
    ValuationMetrics,
)

from app.utils.math_utils import (
    average,
    calculate_cagr,
    safe_divide,
    trend_direction,
)


class MetricsService:
    """
    Generates normalized investment metrics
    from company financial statements.
    """

    @staticmethod
    def build_metrics(
        history: FinancialHistoryResponse,
        financials: FinancialsResponse,
    ) -> MetricsResponse:
        """
        Build all investment metrics.

        Parameters
        ----------
        history
            Historical financial statements.

        financials
            Latest financial snapshot.

        Returns
        -------
        MetricsResponse
        """

        growth = MetricsService._build_growth(history)

        quality = MetricsService._build_quality(
            history,
            financials,
        )

        financial_strength = MetricsService._build_financial_strength(
            history,
            financials,
        )

        valuation = MetricsService._build_valuation(
            financials,
        )

        return MetricsResponse(
            symbol=history.symbol,
            growth=growth,
            quality=quality,
            financial_strength=financial_strength,
            valuation=valuation,
                )

    @staticmethod
    def _build_growth(
        history: FinancialHistoryResponse,
    ) -> GrowthMetrics:
        """
        Calculate growth metrics from historical financial statements.
        """

        annual = history.annual

        if len(annual) < 2:
            return GrowthMetrics()

        revenues = [item.revenue for item in annual]
        eps_values = [item.eps for item in annual]
        fcf_values = [item.free_cash_flow for item in annual]

        revenue_cagr_1y = None
        revenue_cagr_3y = None
        revenue_cagr_5y = None

        eps_cagr_3y = None
        free_cash_flow_cagr_3y = None

        # Revenue CAGR

        if len(revenues) >= 2:
            revenue_cagr_1y = calculate_cagr(
                revenues[1],
                revenues[0],
                1,
            )

        if len(revenues) >= 4:
            revenue_cagr_3y = calculate_cagr(
                revenues[3],
                revenues[0],
                3,
            )

        if len(revenues) >= 6:
            revenue_cagr_5y = calculate_cagr(
                revenues[5],
                revenues[0],
                5,
            )

        # EPS CAGR

        if len(eps_values) >= 4:
            eps_cagr_3y = calculate_cagr(
                eps_values[3],
                eps_values[0],
                3,
            )

        # Free Cash Flow CAGR

        if len(fcf_values) >= 4:
            free_cash_flow_cagr_3y = calculate_cagr(
                fcf_values[3],
                fcf_values[0],
                3,
            )

        return GrowthMetrics(
            revenue_cagr_1y=revenue_cagr_1y,
            revenue_cagr_3y=revenue_cagr_3y,
            revenue_cagr_5y=revenue_cagr_5y,
            eps_cagr_3y=eps_cagr_3y,
            free_cash_flow_cagr_3y=free_cash_flow_cagr_3y,
        )

    @staticmethod
    def _build_quality(
        history: FinancialHistoryResponse,
        financials: FinancialsResponse,
    ) -> QualityMetrics:
        """
        Calculate quality metrics.
        """

        annual = history.annual

        roe = financials.return_on_equity

        margins = []
        revenues = []
        earnings = []

        for item in annual:

            if item.revenue is not None:
                revenues.append(item.revenue)

            if item.net_income is not None:
                earnings.append(item.net_income)

            if (
                item.revenue is not None
                and item.net_income is not None
                and item.revenue != 0
            ):
                margins.append(
                    (item.net_income / item.revenue) * 100
                )

        earnings_stability = None
        revenue_stability = None

        if len(earnings) >= 2:
            increasing = sum(
                1
                for i in range(len(earnings) - 1)
                if earnings[i] >= earnings[i + 1]
            )

            earnings_stability = round(
                (increasing / (len(earnings) - 1)) * 100,
                2,
            )

        if len(revenues) >= 2:
            increasing = sum(
                1
                for i in range(len(revenues) - 1)
                if revenues[i] >= revenues[i + 1]
            )

            revenue_stability = round(
                (increasing / (len(revenues) - 1)) * 100,
                2,
            )

        return QualityMetrics(
            average_roe=roe,
            average_profit_margin=average(margins),
            earnings_stability=earnings_stability,
            revenue_stability=revenue_stability,
        )

    @staticmethod
    def _build_financial_strength(
        history: FinancialHistoryResponse,
        financials: FinancialsResponse,
    ) -> FinancialStrengthMetrics:
        """
        Calculate financial strength metrics.
        """

        annual = history.annual

        latest = annual[0] if annual else None
        oldest = annual[-1] if annual else None

        cash_to_debt = safe_divide(
            financials.cash,
            financials.total_debt,
        )

        debt_trend = None

        if (
            latest
            and oldest
            and latest.total_debt is not None
            and oldest.total_debt is not None
        ):
            debt_trend = trend_direction(
                oldest.total_debt,
                latest.total_debt,
            )

        equity_growth_3y = None

        if len(annual) >= 4:
            equity_growth_3y = calculate_cagr(
                annual[3].shareholders_equity,
                annual[0].shareholders_equity,
                3,
            )

        return FinancialStrengthMetrics(
            cash_to_debt=cash_to_debt,
            debt_trend=debt_trend,
            equity_growth_3y=equity_growth_3y,
            current_ratio=financials.current_ratio,
        )
    @staticmethod
    def _build_valuation(
        financials: FinancialsResponse,
    ) -> ValuationMetrics:
        """
        Calculate valuation metrics.
        """

        return ValuationMetrics(
            pe=financials.trailing_pe,
            peg=financials.peg_ratio,
            price_to_book=financials.price_to_book,
        )