from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str


class ErrorResponse(BaseModel):
    detail: str


class QuoteResponse(BaseModel):
    symbol: str
    company_name: str

    current_price: float

    currency: Optional[str] = None
    exchange: Optional[str] = None

    market_cap: Optional[int] = None

    previous_close: Optional[float] = None

    # 3-month average daily traded volume (shares)
    average_volume_90d: Optional[int] = None


class SearchResult(BaseModel):
    symbol: str
    name: str
    exchange: Optional[str] = None
    quote_type: Optional[str] = None
    currency: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult]


class HistoricalPrice(BaseModel):
    date: date
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    adj_close: Optional[float] = None
    volume: Optional[int] = None


class HistoricalResponse(BaseModel):
    data_status: str = "available"
    unavailable_reason: Optional[str] = None
    symbol: str
    period: str
    interval: str
    prices: List[HistoricalPrice]
