import logging
from datetime import date
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.models.quote import HistoricalPrice, HistoricalResponse, QuoteResponse
from app.services.price_validator import PriceValidator, PriceValidationResult

logging.basicConfig(level=logging.DEBUG)


class TestPriceValidatorCorporateActions:
    def test_normal_price_movement_valid(self):
        quote = QuoteResponse(
            symbol="RELIANCE.NS",
            company_name="Reliance Industries",
            current_price=2500.0,
            previous_close=2480.0,
        )
        history = HistoricalResponse(
            symbol="RELIANCE.NS",
            period="1mo",
            interval="1d",
            prices=[
                HistoricalPrice(
                    date=date(2024, 1, 15),
                    close=2480.0,
                    adj_close=2480.0,
                )
            ],
        )
        result = PriceValidator.validate_quote(quote, history)
        assert result.status == "VALID"
        assert result.anomaly.discrepancy_pct == pytest.approx(0.81, rel=1e-2)

    def test_genuine_large_mismatch_invalid(self):
        quote = QuoteResponse(
            symbol="FAKE.NS",
            company_name="Fake Company",
            current_price=10.0,
            previous_close=500.0,
        )
        history = HistoricalResponse(
            symbol="FAKE.NS",
            period="1mo",
            interval="1d",
            prices=[
                HistoricalPrice(
                    date=date(2024, 1, 15),
                    close=500.0,
                    adj_close=500.0,
                )
            ],
        )
        result = PriceValidator.validate_quote(quote, history)
        assert result.status == "INVALID"
        assert result.anomaly.discrepancy_pct == pytest.approx(98.0, rel=1e-2)

    def test_split_adjusted_price_validation(self):
        quote = QuoteResponse(
            symbol="SPLIT.NS",
            company_name="Split Company",
            current_price=500.0,
            previous_close=500.0,
        )
        history = HistoricalResponse(
            symbol="SPLIT.NS",
            period="1mo",
            interval="1d",
            prices=[
                HistoricalPrice(
                    date=date(2024, 1, 15),
                    close=250.0,
                    adj_close=500.0,
                )
            ],
        )
        result = PriceValidator.validate_quote(quote, history)
        assert result.status == "VALID"
        assert result.anomaly.discrepancy_pct == pytest.approx(0.0, rel=1e-2)
        assert any("Corporate action detected" in r for r in result.reasons)

    def test_bonus_issue_adjusted_price_validation(self):
        quote = QuoteResponse(
            symbol="BONUS.NS",
            company_name="Bonus Company",
            current_price=200.0,
            previous_close=200.0,
        )
        history = HistoricalResponse(
            symbol="BONUS.NS",
            period="1mo",
            interval="1d",
            prices=[
                HistoricalPrice(
                    date=date(2024, 1, 15),
                    close=100.0,
                    adj_close=200.0,
                )
            ],
        )
        result = PriceValidator.validate_quote(quote, history)
        assert result.status == "VALID"
        assert result.anomaly.discrepancy_pct == pytest.approx(0.0, rel=1e-2)
        assert any("Corporate action detected" in r for r in result.reasons)

    def test_missing_adj_close_conservative_behavior(self):
        quote = QuoteResponse(
            symbol="NODATA.NS",
            company_name="No Adj Close",
            current_price=600.0,
            previous_close=500.0,
        )
        history = HistoricalResponse(
            symbol="NODATA.NS",
            period="1mo",
            interval="1d",
            prices=[
                HistoricalPrice(
                    date=date(2024, 1, 15),
                    close=500.0,
                    adj_close=None,
                )
            ],
        )
        result = PriceValidator.validate_quote(quote, history)
        assert result.status == "REVIEW_REQUIRED"
        assert result.anomaly.discrepancy_pct == pytest.approx(20.0, rel=1e-2)
        assert any(
            "No adj_close available" in r or "corporate action unknown" in r
            for r in result.reasons
        )

    def test_large_mismatch_even_after_adjustment_invalid(self):
        quote = QuoteResponse(
            symbol="BAD.NS",
            company_name="Bad Data",
            current_price=10.0,
            previous_close=500.0,
        )
        history = HistoricalResponse(
            symbol="BAD.NS",
            period="1mo",
            interval="1d",
            prices=[
                HistoricalPrice(
                    date=date(2024, 1, 15),
                    close=250.0,
                    adj_close=500.0,
                )
            ],
        )
        result = PriceValidator.validate_quote(quote, history)
        assert result.status == "INVALID"
        assert any("corporate-action adjustment" in r for r in result.reasons)

    def test_review_threshold_with_adjustment(self):
        quote = QuoteResponse(
            symbol="REVIEW.NS",
            company_name="Review Company",
            current_price=576.0,
            previous_close=500.0,
        )
        history = HistoricalResponse(
            symbol="REVIEW.NS",
            period="1mo",
            interval="1d",
            prices=[
                HistoricalPrice(
                    date=date(2024, 1, 15),
                    close=500.0,
                    adj_close=500.0,
                )
            ],
        )
        result = PriceValidator.validate_quote(quote, history)
        assert result.status == "REVIEW_REQUIRED"
        assert result.anomaly.discrepancy_pct == pytest.approx(15.2, rel=1e-2)

    def test_no_history_returns_valid(self):
        quote = QuoteResponse(
            symbol="NOHIST.NS",
            company_name="No History",
            current_price=100.0,
        )
        result = PriceValidator.validate_quote(quote, None)
        assert result.status == "VALID"
        assert any("No historical data" in r for r in result.reasons)

    def test_nan_price_invalid(self):
        quote = QuoteResponse(
            symbol="NAN.NS",
            company_name="NaN Price",
            current_price=float("nan"),
        )
        result = PriceValidator.validate_quote(quote, None)
        assert result.status == "INVALID"

    def test_negative_price_invalid(self):
        quote = QuoteResponse(
            symbol="NEG.NS",
            company_name="Negative Price",
            current_price=-10.0,
        )
        result = PriceValidator.validate_quote(quote, None)
        assert result.status == "INVALID"
