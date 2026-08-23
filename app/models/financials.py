from typing import Optional

from pydantic import BaseModel


class FinancialsResponse(BaseModel):
    data_status: str = "available"
    unavailable_reason: Optional[str] = None
    symbol: Optional[str] = None
    company_name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[str] = None
    market_cap: Optional[int] = None
    enterprise_value: Optional[int] = None
    shares_outstanding: Optional[int] = None
    employees: Optional[int] = None
    revenue_ttm: Optional[float] = None
    gross_profit: Optional[float] = None
    ebitda: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    free_cash_flow: Optional[float] = None
    capital_expenditure: Optional[float] = None
    cash: Optional[float] = None
    total_debt: Optional[float] = None
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    total_equity: Optional[float] = None
    book_value_per_share: Optional[float] = None
    eps: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    price_to_book: Optional[float] = None
    return_on_equity: Optional[float] = None
    return_on_assets: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    profit_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    dividend_yield: Optional[float] = None
