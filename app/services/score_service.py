from __future__ import annotations
from typing import List

from app.models.metrics import MetricsResponse
from app.models.score import ScoreResponse, ScoreSummary


class ScoreService:
    """
    Converts financial metrics into a simple POFIT MVP score.
    """

    MODEL_VERSION = "v1"

    def build_score(self, metrics: MetricsResponse) -> ScoreResponse:
        growth_score = self._growth_score(metrics)
        quality_score = self._quality_score(metrics)
        financial_strength_score = self._financial_strength_score(metrics)
        valuation_score = self._valuation_score(metrics)

        overall_score = round(
            (
                growth_score * 0.30
                + quality_score * 0.30
                + financial_strength_score * 0.25
                + valuation_score * 0.15
            ),
            2,
        )

        return ScoreResponse(
            symbol=metrics.symbol,
            model_version=self.MODEL_VERSION,
            overall_score=overall_score,
            grade=self._grade(overall_score),
            growth_score=round(growth_score, 2),
            quality_score=round(quality_score, 2),
            financial_strength_score=round(financial_strength_score, 2),
            valuation_score=round(valuation_score, 2),
            summary=ScoreSummary(
                growth=self._label(growth_score),
                quality=self._label(quality_score),
                financial_strength=self._label(financial_strength_score),
                valuation=self._valuation_label(valuation_score),
            ),
        )

    def _growth_score(self, metrics: MetricsResponse) -> float:
        values = []

        if metrics.growth.revenue_cagr_3y is not None:
            values.append(
                self._scale_positive(
                    metrics.growth.revenue_cagr_3y,
                    25,
                )
            )

        if metrics.growth.eps_cagr_3y is not None:
            values.append(
                self._scale_positive(
                    metrics.growth.eps_cagr_3y,
                    25,
                )
            )

        if metrics.growth.free_cash_flow_cagr_3y is not None:
            values.append(
                self._scale_positive(
                    metrics.growth.free_cash_flow_cagr_3y,
                    25,
                )
            )

        return self._average(values)

    def _quality_score(self, metrics: MetricsResponse) -> float:
        values = []

        if metrics.quality.average_roe is not None:
            values.append(
                self._scale_positive(
                    metrics.quality.average_roe * 100,
                    25,
                )
            )

        if metrics.quality.average_profit_margin is not None:
            values.append(
                self._scale_positive(
                    metrics.quality.average_profit_margin,
                    25,
                )
            )

        if metrics.quality.earnings_stability is not None:
            values.append(metrics.quality.earnings_stability)

        if metrics.quality.revenue_stability is not None:
            values.append(metrics.quality.revenue_stability)

        return self._average(values) 
    def _financial_strength_score(self, metrics: MetricsResponse) -> float:
        values = []

        if metrics.financial_strength.cash_to_debt is not None:
            values.append(
                self._scale_positive(
                    metrics.financial_strength.cash_to_debt,
                    3,
                )
            )

        if metrics.financial_strength.current_ratio is not None:
            values.append(
                self._scale_positive(
                    metrics.financial_strength.current_ratio,
                    3,
                )
            )

        if metrics.financial_strength.equity_growth_3y is not None:
            values.append(
                self._scale_positive(
                    metrics.financial_strength.equity_growth_3y,
                    20,
                )
            )

        debt_trend = metrics.financial_strength.debt_trend

        if debt_trend == "Decreasing":
            values.append(100)

        elif debt_trend == "Stable":
            values.append(70)

        elif debt_trend == "Increasing":
            values.append(30)

        return self._average(values)

    def _valuation_score(self, metrics: MetricsResponse) -> float:
        values = []

        if metrics.valuation.pe is not None:
            values.append(
                self._scale_inverse(
                    metrics.valuation.pe,
                    10,
                    50,
                )
            )

        if metrics.valuation.peg is not None:
            values.append(
                self._scale_inverse(
                    metrics.valuation.peg,
                    1,
                    3,
                )
            )

        if metrics.valuation.price_to_book is not None:
            values.append(
                self._scale_inverse(
                    metrics.valuation.price_to_book,
                    2,
                    10,
                )
            )

        return self._average(values)

    @staticmethod
    def _average(values: List[float]) -> float:
        if not values:
            return 0.0

        return sum(values) / len(values)
    @staticmethod
    def _scale_positive(value: float, target: float) -> float:
        """
        Higher values are better.

        target = value that receives 100 points.
        """

        if value <= 0:
            return 0.0

        score = (value / target) * 100

        return max(0.0, min(100.0, score))

    @staticmethod
    def _scale_inverse(value: float, ideal: float, maximum: float) -> float:
        """
        Lower values are better.
        """

        if value <= ideal:
            return 100.0

        if value >= maximum:
            return 0.0

        score = ((maximum - value) / (maximum - ideal)) * 100

        return max(0.0, min(100.0, score))

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 90:
            return "A+"

        if score >= 80:
            return "A"

        if score >= 70:
            return "B"

        if score >= 60:
            return "C"

        if score >= 50:
            return "D"

        return "F"
    @staticmethod
    def _label(score: float) -> str:
        if score >= 90:
            return "Exceptional"

        if score >= 80:
            return "Strong"

        if score >= 70:
            return "Good"

        if score >= 60:
            return "Average"

        return "Weak"

    @staticmethod
    def _valuation_label(score: float) -> str:
        if score >= 90:
            return "Very Cheap"

        if score >= 80:
            return "Cheap"

        if score >= 60:
            return "Fair"

        if score >= 40:
            return "Expensive"

        return "Very Expensive"