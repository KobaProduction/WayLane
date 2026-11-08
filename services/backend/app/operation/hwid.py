from uuid import UUID

from app.db import AsyncSession
from app.db.crud.hwid import delete_subscription_hwid, get_subscription_hwids, reset_subscription_hwids
from app.models.admin import AdminDetails
from app.models.subscription_models import SubscriptionHWIDListResponse, SubscriptionHWIDResponse
from app.operation import BaseOperation
from app.operation.permissions import PermissionDenied, enforce_permission


class HWIDOperation(BaseOperation):
    async def _get_hwid_user(self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails, *scope_actions: str):
        """Resolve the user whose HWIDs are accessed. The routes gate on hwids.*, which carries no
        ownership scope, so the admin must also be able to see the user: users.read is required (a
        missing action would otherwise mean "unrestricted") and every given users.* scope must hold."""
        try:
            enforce_permission(admin, "subscriptions", "read")
        except PermissionDenied:
            await self.raise_error(message="Subscription not found", code=404)
        db_user = None
        for scope_action in scope_actions:
            db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin, scope_action=scope_action)
        return db_user

    async def get_subscription_hwids(
        self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails
    ) -> SubscriptionHWIDListResponse:
        db_user = await self._get_hwid_user(db, subscription_uuid, admin, "read")
        hwids = await get_subscription_hwids(db, db_user.uuid)
        hwid_responses = [SubscriptionHWIDResponse.model_validate(h) for h in hwids]
        return SubscriptionHWIDListResponse(hwids=hwid_responses, count=len(hwid_responses))

    async def delete_subscription_hwid(self, db: AsyncSession, subscription_uuid: UUID, hwid: str, admin: AdminDetails) -> dict:
        db_user = await self._get_hwid_user(db, subscription_uuid, admin, "read", "delete")
        deleted = await delete_subscription_hwid(db, db_user.uuid, hwid)
        if not deleted:
            await self.raise_error(message="HWID not found", code=404)
        return {}

    async def reset_subscription_hwids(self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails) -> dict:
        db_user = await self._get_hwid_user(db, subscription_uuid, admin, "read", "delete")
        count = await reset_subscription_hwids(db, db_user.uuid)
        return {"count": count}
