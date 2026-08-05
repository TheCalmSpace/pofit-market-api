from typing import Dict

from app.models.explanation import ExplanationResponse
from app.models.score import ScoreResponse


class ExplanationService:
    """Deterministically builds explanations from ScoreResponse values."""

    def build_explanation(self, score: ScoreResponse) -> ExplanationResponse:
        """Create an explanation response from score details."""

        growth = self._growth_reason(score.growth_score)
        quality = self._quality_reason(score.quality_score)
        financial_strength = self._financial_strength_reason(
            score.financial_strength_score
        )
        valuation = self._valuation_reason(score.valuation_score)
        overall_summary = self._build_overall_summary(
            growth, quality, financial_strength, valuation
        )
        selection_reason = self._selection_reason()
        risk_level = self._risk_level(
            score.financial_strength_score,
            score.quality_score,
        )
        confidence = self._confidence(score.overall_score)
        overall_title = self._overall_title(
            score.overall_score,
            score.growth_score,
            score.quality_score,
            score.financial_strength_score,
            score.valuation_score,
        )

        return ExplanationResponse(
            overall_title=overall_title,
            overall_summary=overall_summary,
            selection_reason=selection_reason,
            growth=growth,
            quality=quality,
            financial_strength=financial_strength,
            valuation=valuation,
            risk_level=risk_level,
            confidence=confidence,
        )

    def _growth_reason(self, score: float) -> str:
        if score >= 90:
            return "Exceptional long-term revenue and earnings growth."
        if score >= 80:
            return "Strong and consistent long-term growth."
        if score >= 65:
            return "Healthy growth profile."
        if score >= 50:
            return "Moderate growth."
        return "Weak long-term growth characteristics."

    def _quality_reason(self, score: float) -> str:
        if score >= 90:
            return "Exceptional business quality with consistently strong returns."
        if score >= 80:
            return "High quality business with durable competitive advantages."
        if score >= 65:
            return "Good operating quality."
        if score >= 50:
            return "Average business quality."
        return "Below-average quality metrics."

    def _financial_strength_reason(self, score: float) -> str:
        if score >= 90:
            return "Outstanding balance sheet with very low financial risk."
        if score >= 80:
            return "Strong balance sheet."
        if score >= 65:
            return "Healthy financial position."
        if score >= 50:
            return "Moderate financial strength."
        return "Financial position requires caution."

    def _valuation_reason(self, score: float) -> str:
        if score >= 90:
            return "Trading significantly below estimated fair value."
        if score >= 80:
            return "Appears undervalued."
        if score >= 65:
            return "Trading close to fair value."
        if score >= 50:
            return "Slightly expensive."
        return "Currently expensive relative to fundamentals."

    def _build_overall_summary(
        self,
        growth: str,
        quality: str,
        financial_strength: str,
        valuation: str,
    ) -> str:
        return (
            "This company combines "
            f"{quality.lower()} with {growth.lower()} "
            f"while maintaining {financial_strength.lower()} "
            f"and {valuation.lower()}"
        ).replace("  ", " ")

    def _selection_reason(self) -> str:
        return (
            "Selected for the Alpha Portfolio because it combines "
            "sustainable growth, high business quality and attractive "
            "long-term investment characteristics."
        )

    def _risk_level(self, financial_strength: float, quality: float) -> str:
        if financial_strength >= 90 and quality >= 90:
            return "Very Low"
        if financial_strength >= 80 and quality >= 80:
            return "Low"
        if financial_strength >= 65 and quality >= 65:
            return "Moderate"
        if financial_strength >= 50 or quality >= 50:
            return "High"
        return "Very High"

    def _confidence(self, overall: float) -> str:
        if overall >= 90:
            return "Very High"
        if overall >= 80:
            return "High"
        if overall >= 70:
            return "Medium"
        return "Low"

    def _overall_title(
        self,
        overall: float,
        growth: float,
        quality: float,
        financial_strength: float,
        valuation: float,
    ) -> str:
        if overall >= 90:
            return "Exceptional Compounder"
        if quality >= 85 and growth >= 80:
            return "High Quality Growth Company"
        if overall >= 80 and valuation >= 70:
            return "Strong GARP Opportunity"
        if overall >= 70:
            return "Balanced Long-Term Investment"
        if growth >= 80:
            return "Emerging Growth Candidate"
        return "Speculative Opportunity"
