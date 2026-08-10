from fastapi import HTTPException, status

from app.repositories.subscription_repository import SubscriptionRepository

subscription_repository = SubscriptionRepository()


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
