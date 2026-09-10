from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from app.dependencies import (
    get_optional_authenticated_user,
    require_authenticated_user,
)
from app.services.daily_top_picks_service import (
    DailyTopPicksService,
)

router = APIRouter(
    prefix="/daily-top-picks",
    tags=["Daily Top Picks"],
)

service = DailyTopPicksService()


@router.post("/generate", dependencies=[Depends(require_authenticated_user)])
def generate():

    return service.generate_all()


def _visible_limit(user: Optional[Dict[str, Any]]) -> int:
    return 50 if user else 10


@router.get("/india", response_model=List[dict])
def india(user: Optional[Dict[str, Any]] = Depends(get_optional_authenticated_user)):

    return service.get_country(
        "IN",
        limit=_visible_limit(user),
    )


@router.get("/usa", response_model=List[dict])
def usa(user: Optional[Dict[str, Any]] = Depends(get_optional_authenticated_user)):

    return service.get_country(
        "US",
        limit=_visible_limit(user),
    )