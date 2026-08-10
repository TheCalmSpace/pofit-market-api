from typing import Any, Dict, Optional

from app.core.supabase import supabase


class SubscriptionRepository:
    """Repository for managing user subscriptions in Supabase."""

    def get_by_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        result = (
            supabase.table("subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    def create_free_subscription(self, user_id: str) -> Dict[str, Any]:
        existing = self.get_by_user(user_id)
        if existing:
            return existing

        payload = {
            "user_id": user_id,
            "plan": "free",
            "status": "active",
            "is_lifetime": False,
        }

        result = supabase.table("subscriptions").insert(payload).execute()

        if not result.data:
            raise RuntimeError("Failed to create free subscription")

        return result.data[0]

    def activate_subscription(
        self,
        user_id: str,
        plan: Optional[str] = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        payload = {
            "status": status,
        }

        if plan:
            payload["plan"] = plan

        result = (
            supabase.table("subscriptions")
            .update(payload)
            .eq("user_id", user_id)
            .execute()
        )

        if result.data:
            return result.data[0]

        create_payload = {
            "user_id": user_id,
            "plan": plan or "free",
            "status": status,
            "is_lifetime": False,
        }

        insert_result = supabase.table("subscriptions").insert(create_payload).execute()

        if not insert_result.data:
            raise RuntimeError("Failed to activate subscription")

        return insert_result.data[0]

    def mark_lifetime(
        self,
        user_id: str,
        is_lifetime: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "is_lifetime": is_lifetime,
        }

        if is_lifetime:
            payload.update(
                {
                    "plan": "pro",
                    "status": "active",
                    "expires_at": None,
                }
            )

        result = (
            supabase.table("subscriptions")
            .update(payload)
            .eq("user_id", user_id)
            .execute()
        )

        if result.data:
            return result.data[0]

        create_payload = {
            "user_id": user_id,
            "plan": "pro" if is_lifetime else "free",
            "status": "active",
            "is_lifetime": is_lifetime,
            "expires_at": None if is_lifetime else None,
        }

        insert_result = supabase.table("subscriptions").insert(create_payload).execute()

        if not insert_result.data:
            raise RuntimeError("Failed to mark subscription lifetime")

        return insert_result.data[0]

    def grant_lifetime(self, user_id: str) -> Dict[str, Any]:
        """Grant a lifetime subscription to a user.

        This ensures the subscription is stored as a pro plan with an active
        status, lifetime entitlement, and no expiration date.
        """
        payload = {
            "plan": "pro",
            "status": "active",
            "is_lifetime": True,
            "expires_at": None,
        }

        result = (
            supabase.table("subscriptions")
            .update(payload)
            .eq("user_id", user_id)
            .execute()
        )

        if result.data:
            return result.data[0]

        create_payload = {
            "user_id": user_id,
            **payload,
        }

        insert_result = supabase.table("subscriptions").insert(create_payload).execute()

        if not insert_result.data:
            raise RuntimeError("Failed to grant lifetime subscription")

        return insert_result.data[0]

    def update_subscription(
        self,
        user_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not updates:
            raise ValueError("updates cannot be empty")

        result = (
            supabase.table("subscriptions")
            .update(updates)
            .eq("user_id", user_id)
            .execute()
        )

        if result.data:
            return result.data[0]

        create_payload = {
            "user_id": user_id,
            "plan": "free",
            "status": "active",
            "is_lifetime": False,
            **updates,
        }

        insert_result = supabase.table("subscriptions").insert(create_payload).execute()

        if not insert_result.data:
            raise RuntimeError("Failed to update subscription")

        return insert_result.data[0]
