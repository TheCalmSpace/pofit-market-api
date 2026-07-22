from typing import List, Optional

from pydantic import BaseModel


class FinancialHistoryAnnualItem(BaseModel):
    year: int
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    free_cash_flow: Optional[float] = None
    capital_expenditure: Optional[float] = None
    cash: Optional[float] = None
    total_debt: Optional[float] = None
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    shareholders_equity: Optional[float] = None
    eps: Optional[float] = None


class FinancialHistoryResponse(BaseModel):
    symbol: str
    currency: Optional[str] = None
    annual: List[FinancialHistoryAnnualItem]
