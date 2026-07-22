from fastapi import FastAPI

from app.models import HealthResponse
from app.routers import financial_history, financials, historical, quote, search

API_VERSION = "1.0.0"


def create_app() -> FastAPI:
    application = FastAPI(
        title="POFIT Market API",
        description="Market data API for the POFIT stock analysis platform.",
        version=API_VERSION,
    )

    application.include_router(quote.router)
    application.include_router(search.router)
    application.include_router(historical.router)
    application.include_router(financials.router)
    application.include_router(financial_history.router)

    @application.get("/", response_model=HealthResponse, tags=["Health"])
    def health_check() -> HealthResponse:
        return HealthResponse(
            status="POFIT Market API is running",
            version=API_VERSION,
        )

    return application


app = create_app()
