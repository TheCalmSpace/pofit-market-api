"""
Pydantic models for the POFIT Metrics API.
"""

from typing import Optional

from pydantic import BaseModel


class GrowthMetrics(BaseModel):
    revenue_cagr_1y: Optional[float] = None
    revenue_cagr_3y: Optional[float] = None
    revenue_cagr_5y: Optional[float] = None

    eps_cagr_3y: Optional[float] = None

    free_cash_flow_cagr_3y: Optional[float] = None


class QualityMetrics(BaseModel):
    average_roe: Optional[float] = None

    average_profit_margin: Optional[float] = None

    earnings_stability: Optional[float] = None

    revenue_stability: Optional[float] = None


class FinancialStrengthMetrics(BaseModel):
    cash_to_debt: Optional[float] = None

    debt_trend: Optional[str] = None

    equity_growth_3y: Optional[float] = None

    current_ratio: Optional[float] = None


class ValuationMetrics(BaseModel):
    pe: Optional[float] = None

    relative_pe: Optional[float] = None

    peg: Optional[float] = None

    price_to_book: Optional[float] = None


class MetricsResponse(BaseModel):
    data_status: str = "available"
    unavailable_reason: Optional[str] = None
    symbol: str

    growth: GrowthMetrics

    quality: QualityMetrics

    financial_strength: FinancialStrengthMetrics

    valuation: ValuationMetrics
