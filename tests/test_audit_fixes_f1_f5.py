import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import HTTPException
from app.api.routes.users import (
    get_my_email_preferences,
    patch_my_email_preferences,
    get_my_shiftpilot_state,
    upsert_my_shiftpilot_state,
    list_my_onboarding_v2_records,
    create_my_onboarding_v2_record,
    upsert_my_latest_onboarding_v2_record,
    apply_my_latest_onboarding_v2_record,
    get_admin_summary,
    list_users,
)
from app.schemas.email_center import EmailPreferenceUpdate
from app.schemas.onboarding_v2 import OnboardingV2RecordCreateIn
from app.schemas.shiftpilot import ShiftPilotStateUpsertIn


class TestAuditFixesF(unittest.IsolatedAsyncioTestCase):
    async def test_f1_email_preferences_locked_for_guests(self):
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_db = AsyncMock()

        # GET /me/email-preferences
        with self.assertRaises(HTTPException) as ctx:
            await get_my_email_preferences(db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")
        self.assertEqual(ctx.exception.detail.get("feature"), "notifications")

        # PATCH /me/email-preferences
        with self.assertRaises(HTTPException) as ctx:
            await patch_my_email_preferences(
                payload=EmailPreferenceUpdate(tips_enabled=True),
                db=mock_db,
                current_user=mock_user,
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")

    async def test_f2_shiftpilot_locked_for_guests_with_wall_hit(self):
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_db = AsyncMock()
        mock_db.add = MagicMock()

        # GET /me/shiftpilot-state
        with self.assertRaises(HTTPException) as ctx:
            await get_my_shiftpilot_state(db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")
        self.assertEqual(ctx.exception.detail.get("feature"), "reports")
        self.assertTrue(mock_db.add.called)
        event = mock_db.add.call_args[0][0]
        self.assertEqual(event.name, "guest_wall_hit")
        self.assertEqual(event.meta.get("wall"), "reports")

        # PUT /me/shiftpilot-state
        mock_db.add.reset_mock()
        with self.assertRaises(HTTPException) as ctx:
            await upsert_my_shiftpilot_state(
                payload=ShiftPilotStateUpsertIn(payload={"template": "balanced"}),
                db=mock_db,
                current_user=mock_user,
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")
        self.assertTrue(mock_db.add.called)

    async def test_f3_onboarding_v2_endpoints_locked_or_empty_for_guests(self):
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_db = AsyncMock()

        # GET /me/onboarding-v2-records returns []
        records = await list_my_onboarding_v2_records(limit=20, db=mock_db, current_user=mock_user)
        self.assertEqual(records, [])

        # POST /me/onboarding-v2-records raises 403
        payload = OnboardingV2RecordCreateIn(answers={}, draft_objects={})
        with self.assertRaises(HTTPException) as ctx:
            await create_my_onboarding_v2_record(payload, db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")
        self.assertEqual(ctx.exception.detail.get("feature"), "money-plan")

        # PUT /me/onboarding-v2-records/latest raises 403
        with self.assertRaises(HTTPException) as ctx:
            await upsert_my_latest_onboarding_v2_record(payload, db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")

        # POST /me/onboarding-v2-records/latest/apply raises 403
        with self.assertRaises(HTTPException) as ctx:
            await apply_my_latest_onboarding_v2_record(db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")

    async def test_f4_admin_summary_and_list_users_filters_guests(self):
        mock_admin = MagicMock()
        mock_admin.id = uuid4()
        mock_admin.role = "superadmin"
        mock_admin.is_guest = False
        mock_db = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_res
        mock_db.scalar.return_value = 10

        # get_admin_summary
        summary = await get_admin_summary(db=mock_db, current_user=mock_admin)
        user_stmt = str(mock_db.scalar.call_args_list[0][0][0])
        self.assertIn("users.is_guest IS false", user_stmt)
        self.assertIn("users.deleted_at IS NULL", user_stmt)
        self.assertEqual(summary.users, 10)

        # list_users with default include_guests=False
        await list_users(limit=50, offset=0, include_guests=False, db=mock_db, current_user=mock_admin)
        list_stmt = str(mock_db.execute.call_args[0][0])
        self.assertIn("users.is_guest IS false", list_stmt)


if __name__ == "__main__":
    unittest.main()
