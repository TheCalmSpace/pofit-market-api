from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.dependencies import require_authenticated_user
from app.services.subscription_service import SubscriptionService

router = APIRouter(
    prefix="/subscription",
    tags=["Subscription"],
    dependencies=[Depends(require_authenticated_user)],
)

subscription_service = SubscriptionService()


class SubscriptionResponse(BaseModel):
    plan: str
    status: str
    is_premium: bool
    is_lifetime: bool
    expires_at: Optional[str] = None


@router.get(
    "/{user_id}",
    response_model=SubscriptionResponse,
)
def get_subscription(
    user_id: str,
    user: dict = Depends(require_authenticated_user),
) -> SubscriptionResponse:
    if user_id != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another user's subscription.",
        )
    subscription = subscription_service.get_subscription(user_id)

    return SubscriptionResponse(
        plan=subscription.get("plan") or "free",
        status=subscription.get("status") or "active",
        is_premium=subscription_service.is_premium(user_id),
        is_lifetime=bool(subscription.get("is_lifetime")),
        expires_at=subscription.get("expires_at"),
    )

@router.post(
    "/lifetime/{user_id}",
    response_model=SubscriptionResponse,
)
def grant_lifetime_subscription(
    user_id: str,
    user: dict = Depends(require_authenticated_user),
) -> SubscriptionResponse:
    """Grant a temporary lifetime subscription for development and testing."""
    if user_id != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify another user's subscription.",
        )
    subscription = subscription_service.grant_lifetime(user_id)

    return SubscriptionResponse(
        plan=subscription.get("plan") or "pro",
        status=subscription.get("status") or "active",
        is_premium=subscription_service.is_premium(user_id),
        is_lifetime=bool(subscription.get("is_lifetime")),
        expires_at=subscription.get("expires_at"),
    )