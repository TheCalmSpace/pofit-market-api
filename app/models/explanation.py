from pydantic import BaseModel


class ExplanationResponse(BaseModel):
    overall_title: str
    overall_summary: str
    selection_reason: str
    growth: str
    quality: str
    financial_strength: str
    valuation: str
    risk_level: str
    confidence: str
