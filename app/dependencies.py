from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status

from app.core.supabase import supabase
from app.repositories.subscription_repository import SubscriptionRepository

subscription_repository = SubscriptionRepository()


def get_optional_authenticated_user(request: Request) -> Optional[Dict[str, Any]]:
    """Return the Supabase Auth user for a valid bearer token, if supplied."""
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header.",
        )

    try:
        response = supabase.auth.get_user(token.strip())
        user = getattr(response, "user", None)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        ) from exc

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )

    return {"id": user.id, "email": getattr(user, "email", None)}


def require_authenticated_user(request: Request) -> Dict[str, Any]:
    user = get_optional_authenticated_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return user


def require_premium(user_id: str):
    """Require a premium subscription for the given user.

    Reads the user's subscription and grants access only when the subscription
    status is "active" or when the subscription is flagged as lifetime.

    Raises:
        HTTPException: 403 if the user does not have a premium subscription.
    """
    subscription = subscription_repository.get_by_user(user_id)

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Premium subscription required.",
        )

    if subscription.get("status") == "active" or bool(subscription.get("is_lifetime")):
        return subscription

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Premium subscription required.",
    )
