from typing import Any, Dict, Optional

from app.repositories.subscription_repository import SubscriptionRepository


class SubscriptionService:
    """Service layer for subscription business logic."""

    def __init__(self, repository: Optional[SubscriptionRepository] = None):
        self.repository = repository or SubscriptionRepository()

    def is_premium(self, user_id: str) -> bool:
        subscription = self.get_subscription(user_id)
        if not subscription:
            return False

        return bool(subscription.get("is_lifetime") or self._is_paid_plan(subscription))

    def get_subscription(self, user_id: str) -> Optional[Dict[str, Any]]:
        subscription = self.repository.get_by_user(user_id)
        if subscription:
            return subscription

        return self.repository.create_free_subscription(user_id)

    def activate_subscription(
        self,
        user_id: str,
        plan: Optional[str] = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        return self.repository.activate_subscription(user_id, plan=plan, status=status)

    def grant_lifetime(self, user_id: str) -> Dict[str, Any]:
        """Grant a lifetime subscription to the specified user.

        The lifetime subscription is represented as a pro plan with active
        status, lifetime entitlement, and no expiration date.
        """
        return self.repository.grant_lifetime(user_id)

    def is_subscription_active(
        self,
        user_id: str,
        *,
        include_lifetime: bool = True,
    ) -> bool:
        subscription = self.get_subscription(user_id)
        if not subscription:
            return False

        if include_lifetime and subscription.get("is_lifetime"):
            return True

        return bool(subscription.get("status") == "active") and self._is_paid_plan(subscription)

    def _is_paid_plan(self, subscription: Dict[str, Any]) -> bool:
        plan = (subscription.get("plan") or "").lower()
        return plan in {"pro", "premium", "enterprise", "lifetime"}
