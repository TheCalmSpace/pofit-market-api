from fastapi import APIRouter

from app.services.daily_top_picks_service import (
    DailyTopPicksService,
)

router = APIRouter(
    prefix="/daily-top-picks",
    tags=["Daily Top Picks"],
)

service = DailyTopPicksService()


@router.post("/generate")
def generate():

    return service.generate_all()


@router.get("/india")
def india():

    return service.get_country(
        "IN"
    )


@router.get("/usa")
def usa():

    return service.get_country(
        "US"
    )