from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.models import HealthResponse

from app.routers import (
    financial_history,
    financials,
    historical,
    metrics,
    quote,
    score,
    search,
    daily_top_picks,
)

from app.scheduler import start_scheduler


API_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield


def create_app() -> FastAPI:

    application = FastAPI(
        title="POFIT Market API",
        description="Market data API for the POFIT stock analysis platform.",
        version=API_VERSION,
        lifespan=lifespan,
    )

    application.include_router(
        quote.router
    )

    application.include_router(
        search.router
    )

    application.include_router(
        historical.router
    )

    application.include_router(
        financials.router
    )

    application.include_router(
        financial_history.router
    )

    application.include_router(
        metrics.router
    )

    application.include_router(
        score.router
    )

    application.include_router(
        daily_top_picks.router
    )

    from app.routers import alpha

    application.include_router(
        alpha.router
    )

    @application.get(
        "/",
        response_model=HealthResponse,
        tags=["Health"],
    )
    def health_check() -> HealthResponse:

        return HealthResponse(
            status="POFIT Market API is running",
            version=API_VERSION,
        )

    return application


app = create_app()