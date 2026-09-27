import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from app.api.deps import forbid_guest
from app.api.routes.contact_messages import check_account_existence, submit_contact_message
from app.api.routes.advisor import advisor_accept, advisor_chat
from app.api.routes.gamification import reset_weekly, reset_monthly
from app.schemas.contact_message import ContactMessageCreate
from app.schemas.advisor import AdvisorAcceptRequestIn, AdvisorChatRequestIn, ChatMessageIn


class TestAuditFixesE(unittest.IsolatedAsyncioTestCase):
    async def test_e1_categories_mutation_forbids_guests(self):
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True

        mock_req_post = MagicMock()
        mock_req_post.method = "POST"

        # POST / PUT / PATCH / DELETE should be forbidden for guest
        with self.assertRaises(HTTPException) as ctx:
            await forbid_guest(mock_req_post, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")

        # GET should be allowed (preview)
        mock_req_get = MagicMock()
        mock_req_get.method = "GET"
        allowed_user = await forbid_guest(mock_req_get, current_user=mock_user)
        self.assertEqual(allowed_user.id, mock_user.id)

    async def test_e2_contact_messages_filters_guests(self):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_res = MagicMock()
        mock_res.scalars.return_value.first.return_value = None
        mock_db.execute.return_value = mock_res

        # Test check_account_existence
        await check_account_existence(contact="test@example.com", db=mock_db)
        check_stmt = str(mock_db.execute.call_args[0][0])
        self.assertIn("users.is_guest IS false", check_stmt)
        self.assertIn("users.deleted_at IS NULL", check_stmt)

        # Test submit_contact_message
        payload = ContactMessageCreate(
            full_name="Tester",
            contact_info="test@example.com",
            subject="Question",
            message="Hello support",
        )
        await submit_contact_message(payload, db=mock_db)
        contact_stmt = str(mock_db.execute.call_args[0][0])
        self.assertIn("users.is_guest IS false", contact_stmt)
        self.assertIn("users.deleted_at IS NULL", contact_stmt)

    async def test_e3_advisor_emits_wall_hit_telemetry(self):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True

        # Test advisor_accept envelopes cap
        mock_db.scalar.return_value = 20
        payload_accept = AdvisorAcceptRequestIn(
            user_id=mock_user.id,
            preview_id=uuid4(),
            proposal_id="prop_1",
            validation_id=uuid4(),
            confirm=True,
        )
        with self.assertRaises(HTTPException) as ctx:
            await advisor_accept(payload_accept, db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(mock_db.add.call_count, 1)
        added_accept_event = mock_db.add.call_args[0][0]
        self.assertEqual(added_accept_event.name, "guest_wall_hit")
        self.assertEqual(added_accept_event.meta.get("wall"), "envelopes_cap")

        # Test advisor_chat daily limit
        mock_db.add.reset_mock()
        mock_db.scalar.return_value = 10  # GUEST_ADVISOR_MESSAGES_PER_DAY = 5
        payload_chat = AdvisorChatRequestIn(messages=[ChatMessageIn(role="user", text="What is my budget?")])
        with self.assertRaises(HTTPException) as ctx:
            await advisor_chat(payload_chat, db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(mock_db.add.call_count, 1)
        added_chat_event = mock_db.add.call_args[0][0]
        self.assertEqual(added_chat_event.name, "guest_wall_hit")
        self.assertEqual(added_chat_event.meta.get("wall"), "advisor_daily")

    async def test_e5_gamification_cron_isolates_users(self):
        mock_db = AsyncMock()
        mock_req = MagicMock()
        mock_req.headers = {"x-gamification-token": "secret"}
        mock_req.client.host = "127.0.0.1"

        with patch("app.api.routes.gamification.get_settings") as mock_settings:
            mock_settings.return_value.gamification_cron_token = "secret"

            await reset_weekly(request=mock_req, db=mock_db)
            weekly_stmt = str(mock_db.execute.call_args[0][0])
            self.assertIn("users.is_guest IS false", weekly_stmt)
            self.assertIn("users.deleted_at IS NULL", weekly_stmt)

            await reset_monthly(request=mock_req, db=mock_db)
            monthly_stmt = str(mock_db.execute.call_args[0][0])
            self.assertIn("users.is_guest IS false", monthly_stmt)
            self.assertIn("users.deleted_at IS NULL", monthly_stmt)


if __name__ == "__main__":
    unittest.main()
