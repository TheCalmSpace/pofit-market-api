"""
Pydantic models for the POFIT Score API.
"""

from typing import Optional

from pydantic import BaseModel


class ScoreSummary(BaseModel):
    growth: str
    quality: str
    financial_strength: str
    valuation: str


class ScoreResponse(BaseModel):
    symbol: str

    overall_score: float

    grade: str

    growth_score: float

    quality_score: float

    financial_strength_score: float

    valuation_score: float

    summary: ScoreSummary