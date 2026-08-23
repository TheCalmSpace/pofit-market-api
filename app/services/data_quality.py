from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


class DataQualityAssessor:
    """
    Assesses the quality and freshness of cached market data.

    Returns one of:
    - "fresh"          : data is current and complete
    - "stale"          : data exists but TTL expired
    - "partial"        : data exists but some required fields are missing
    - "invalid"        : data exists but contains NaN/Inf/null price
    - "insufficient"   : required data missing entirely
    """

    CACHE_TTL = timedelta(hours=6)
    FRESHNESS_TOLERANCE = timedelta(minutes=30)

    @classmethod
    def assess_quote(cls, cached: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not cached:
            return {"status": "insufficient", "reason": "No cached quote data"}

        quote = cached.get("quote_json")
        if not isinstance(quote, dict):
            return {"status": "insufficient", "reason": "quote_json is missing"}

        price = quote.get("current_price")
        if price is None:
            return {"status": "partial", "reason": "current_price is null"}

        try:
            price_float = float(price)
            if price_float <= 0 or price_float != price_float:
                return {"status": "invalid", "reason": "current_price is non-positive or NaN"}
        except (TypeError, ValueError):
            return {"status": "invalid", "reason": "current_price is not a valid number"}

        updated = cls._parse_timestamp(cached.get("updated_at"))
        if updated is None:
            return {"status": "partial", "reason": "updated_at is missing or invalid"}

        age = datetime.utcnow() - updated
        if age > cls.CACHE_TTL + cls.FRESHNESS_TOLERANCE:
            return {
                "status": "stale",
                "reason": f"Quote data is {age.total_seconds() / 3600:.1f} hours old",
                "age_hours": round(age.total_seconds() / 3600, 1),
            }

        return {
            "status": "fresh",
            "age_hours": round(age.total_seconds() / 3600, 1),
        }

    @classmethod
    def assess_financials(cls, cached: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not cached:
            return {"status": "insufficient", "reason": "No cached financial data"}

        metrics = cached.get("metrics_json")
        if not isinstance(metrics, dict):
            return {"status": "insufficient", "reason": "metrics_json is missing"}

        required_metrics = [
            "growth",
            "quality",
            "financial_strength",
            "valuation",
        ]
        missing = [m for m in required_metrics if not metrics.get(m)]
        if missing:
            return {
                "status": "partial",
                "reason": f"Missing metric groups: {', '.join(missing)}",
                "missing_fields": missing,
            }

        updated = cls._parse_timestamp(cached.get("updated_at"))
        if updated is None:
            return {"status": "partial", "reason": "updated_at is missing or invalid"}

        age = datetime.utcnow() - updated
        if age > cls.CACHE_TTL + cls.FRESHNESS_TOLERANCE:
            return {
                "status": "stale",
                "reason": f"Financial data is {age.total_seconds() / 3600:.1f} hours old",
                "age_hours": round(age.total_seconds() / 3600, 1),
            }

        return {
            "status": "fresh",
            "age_hours": round(age.total_seconds() / 3600, 1),
        }

    @classmethod
    def assess_score(cls, cached: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not cached:
            return {"status": "insufficient", "reason": "No cached score data"}

        score = cached.get("score_json")
        if not isinstance(score, dict):
            return {"status": "insufficient", "reason": "score_json is missing"}

        overall = score.get("overall_score")
        if overall is None:
            return {"status": "insufficient", "reason": "overall_score is null"}

        updated = cls._parse_timestamp(cached.get("updated_at"))
        if updated is None:
            return {
                "status": "partial",
                "reason": "score exists but updated_at is missing",
                "score": overall,
                "model_version": score.get("model_version"),
            }

        age = datetime.utcnow() - updated
        if age > cls.CACHE_TTL:
            return {
                "status": "stale",
                "reason": f"Score is {age.total_seconds() / 3600:.1f} hours old",
                "age_hours": round(age.total_seconds() / 3600, 1),
                "score": overall,
                "model_version": score.get("model_version"),
                "data_as_of": cached.get("updated_at"),
            }

        quote_quality = cls.assess_quote(cached)
        if quote_quality["status"] in ("stale", "invalid"):
            return {
                "status": quote_quality["status"],
                "reason": f"Underlying quote data is {quote_quality['status']}: {quote_quality.get('reason')}",
                "score": overall,
                "model_version": score.get("model_version"),
                "data_as_of": cached.get("updated_at"),
                "underlying_quality": quote_quality,
            }

        financials_quality = cls.assess_financials(cached)
        if financials_quality["status"] in ("stale", "invalid", "insufficient"):
            return {
                "status": "insufficient",
                "reason": f"Underlying financial data is {financials_quality['status']}: {financials_quality.get('reason')}",
                "score": overall,
                "model_version": score.get("model_version"),
                "data_as_of": cached.get("updated_at"),
                "underlying_quality": financials_quality,
            }

        return {
            "status": "fresh",
            "score": overall,
            "model_version": score.get("model_version"),
            "data_as_of": cached.get("updated_at"),
        }

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None)
        except Exception:
            return None
