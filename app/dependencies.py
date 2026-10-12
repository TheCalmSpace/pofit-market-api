import os
import hmac
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


def require_internal_api_key(request: Request) -> None:
    """Validate X-Internal-API-Key header for service-to-service ingestion.

    Fails closed if the expected secret is not configured.
    Uses constant-time comparison to avoid timing attacks.
    """
    expected = os.getenv("POFIT_INTERNAL_API_KEY")
    if not expected:
        # Fail closed: if secret is not configured, reject all requests
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API authentication not configured.",
        )

    provided = request.headers.get("X-Internal-API-Key")
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing internal API key.",
        )

    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal API key.",
        )


def require_internal_or_user_auth(request: Request) -> Dict[str, Any]:
    """Allow either internal API key or user JWT for internal endpoints."""
    provided = request.headers.get("X-Internal-API-Key")
    expected = os.getenv("POFIT_INTERNAL_API_KEY")

    if expected and provided and hmac.compare_digest(provided, expected):
        return {"type": "internal"}

    # Fall back to user authentication
    return require_authenticated_user(request)


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
