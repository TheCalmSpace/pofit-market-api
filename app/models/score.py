"""
Pydantic models for the POFIT Score API.
"""

from typing import List, Optional

from pydantic import BaseModel


class ScoreSummary(BaseModel):
    growth: str
    quality: str
    financial_strength: str
    valuation: str


class ScoreResponse(BaseModel):
    symbol: str
    data_status: str = "available"
    unavailable_reason: Optional[str] = None

    overall_score: Optional[float] = None

    grade: Optional[str] = None

    growth_score: Optional[float] = None

    quality_score: Optional[float] = None

    financial_strength_score: Optional[float] = None

    valuation_score: Optional[float] = None

    summary: Optional[ScoreSummary] = None

    # Auditability fields
    data_as_of: Optional[str] = None
    calculated_at: Optional[str] = None
    model_version: Optional[str] = None
    data_quality_status: Optional[str] = None
    company_type: Optional[str] = None
    unavailable_metrics: List[str] = []
