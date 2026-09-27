import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from app.services.gamification import apply_transaction_scoring
from app.api.routes.gamification import get_summary, list_logs
from app.api.routes.auth import request_password_reset, web_login_token
from app.api.routes.passkeys import passkey_register_options, passkey_register_verify, list_passkeys
from app.api.routes.users import update_user_profile
from app.schemas.auth import PasswordResetRequestIn
from app.schemas.passkeys import PasskeyRegisterVerifyIn
from app.schemas.user_profile import UserProfileUpdate


class TestAuditFixesD(unittest.IsolatedAsyncioTestCase):
    async def test_d1_apply_transaction_scoring_skips_guests(self):
        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True

        mock_tx = MagicMock()
        mock_tx.amount = 100

        with patch("app.services.gamification.get_or_create_gamification", new_callable=AsyncMock) as mock_get_gamif:
            await apply_transaction_scoring(mock_db, mock_user, mock_tx)
            mock_get_gamif.assert_not_called()

    async def test_d2_gamification_summary_and_logs_for_guests(self):
        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_user.leaderboard_name = None
        mock_user.first_name = "Invité"

        # get_summary for guest should not call get_or_create_gamification
        with patch("app.api.routes.gamification.get_or_create_gamification", new_callable=AsyncMock) as mock_get_gamif:
            summary = await get_summary(db=mock_db, current_user=mock_user)
            mock_get_gamif.assert_not_called()
            self.assertFalse(summary.leaderboard_opt_in)
            self.assertEqual(summary.points_total, 0)
            self.assertEqual(summary.current_streak_days, 0)

        # list_logs for guest returns empty list
        logs = await list_logs(limit=10, db=mock_db, current_user=mock_user)
        self.assertEqual(logs, [])

    async def test_d3_password_reset_ignores_guests(self):
        mock_db = AsyncMock()
        mock_guest = MagicMock()
        mock_guest.id = uuid4()
        mock_guest.email = "guest_12345@example.com"
        mock_guest.is_guest = True
        mock_guest.deleted_at = None
        mock_guest.password_hash = "dummy_hash"

        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_guest
        mock_db.execute.return_value = mock_res

        payload = PasswordResetRequestIn(email="guest_12345@example.com")
        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch("app.api.routes.auth.check_rate_limit", new_callable=AsyncMock) as mock_rl, \
             patch("app.api.routes.auth.send_password_reset_email", new_callable=AsyncMock) as mock_send_email:
            
            mock_rl.return_value.allowed = True
            resp = await request_password_reset(payload, mock_request, mock_db)
            
            self.assertEqual(resp.status, "ok")
            # Email must NOT be dispatched to guest address
            mock_send_email.assert_not_called()

    async def test_d4_passkeys_locked_for_guests(self):
        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True

        mock_request = MagicMock()

        with patch("app.api.routes.passkeys.get_settings") as mock_settings:
            mock_settings.return_value.enable_passkeys = True

            with self.assertRaises(HTTPException) as ctx:
                await passkey_register_options(mock_request, user=mock_user, db=mock_db)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")

            verify_payload = PasskeyRegisterVerifyIn(challenge="test", credential={})
            with self.assertRaises(HTTPException) as ctx:
                await passkey_register_verify(verify_payload, mock_request, user=mock_user, db=mock_db)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")

            passkeys = await list_passkeys(mock_request, user=mock_user, db=mock_db)
            self.assertEqual(passkeys, [])

    async def test_d5_web_login_token_and_profile_locked_for_guests(self):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True

        with self.assertRaises(HTTPException) as ctx:
            await web_login_token(user=mock_user, db=mock_db)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")
        
        # Verify guest_wall_hit(multi_device) event was emitted
        self.assertEqual(mock_db.add.call_count, 1)
        added_event = mock_db.add.call_args[0][0]
        self.assertEqual(added_event.name, "guest_wall_hit")
        self.assertEqual(added_event.meta.get("wall"), "multi_device")

        # Profile update is also locked
        update_payload = UserProfileUpdate(first_name="Test")
        with self.assertRaises(HTTPException) as ctx:
            await update_user_profile(payload=update_payload, db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")


if __name__ == "__main__":
    unittest.main()
