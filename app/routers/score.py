from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import require_authenticated_user
from app.models.score import ScoreResponse
from app.repositories.stock_data_repository import StockDataRepository
from app.services.data_quality import DataQualityAssessor
from app.services.data_quality_gate import DataQualityGate
from app.services.market_data_service import MarketDataService
from app.services.yahoo_service import (
    MarketDataUnavailableError,
    SymbolNotFoundError,
)

router = APIRouter(dependencies=[Depends(require_authenticated_user)])

market_service = MarketDataService()
stock_data_repo = StockDataRepository()


@router.get(
    "/score/{symbol}",
    response_model=ScoreResponse,
)
def get_score(symbol: str):
    try:
        stock = market_service.get_stock(symbol)
    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except MarketDataUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if stock.get("score_json") is None:
        return ScoreResponse(
            symbol=symbol.upper(),
            data_status="unavailable",
            unavailable_reason="Score unavailable because required financial data is incomplete.",
        )

    raw_cache = stock_data_repo.get(symbol.upper())

    # Apply unified data quality gate
    gate_report = DataQualityGate.assess(
        symbol=symbol.upper(),
        quote=stock.get("quote_json"),
        history=None,
        financials=stock.get("financials_json"),
        financial_history=stock.get("financial_history_json"),
        cached=raw_cache,
    )

    if not gate_report.is_scoring_allowed:
        return ScoreResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason=gate_report.overall_status,
            data_as_of=gate_report.data_as_of,
            calculated_at=gate_report.calculated_at,
            data_quality_status=gate_report.overall_status,
            company_type=gate_report.company_type,
            unavailable_metrics=gate_report.unavailable_metrics,
        )

    quality = DataQualityAssessor.assess_score(raw_cache)

    if quality["status"] != "fresh":
        return ScoreResponse(
            symbol=symbol.upper(),
            data_status="insufficient_data",
            unavailable_reason=f"Score data is {quality['status']}: {quality.get('reason')}",
            overall_score=quality.get("score"),
            model_version=quality.get("model_version"),
            data_as_of=quality.get("data_as_of"),
            calculated_at=gate_report.calculated_at,
            data_quality_status=quality["status"],
            company_type=gate_report.company_type,
            unavailable_metrics=gate_report.unavailable_metrics,
        )

    response = ScoreResponse(**stock["score_json"])
    response.data_as_of = gate_report.data_as_of
    response.calculated_at = gate_report.calculated_at
    response.model_version = quality.get("model_version")
    response.data_quality_status = "fresh"
    response.company_type = gate_report.company_type
    response.unavailable_metrics = gate_report.unavailable_metrics
    return response
