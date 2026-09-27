import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.api.routes.auth import recover_guest
from app.api.routes.users import export_user_data
from app.schemas.auth import GuestRecoverIn
from fastapi import HTTPException

class TestAuditFixesB(unittest.IsolatedAsyncioTestCase):
    async def test_b4_recover_guest_checks_kill_existing(self):
        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.deleted_at = None
        mock_user.is_guest = True

        mock_exec_res = MagicMock()
        mock_exec_res.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_exec_res

        mock_ps = MagicMock()
        mock_ps.guest_mode_enabled = False
        mock_ps.guest_mode_kill_existing = True

        with patch("app.api.routes.auth.enforce_rate_limit", new_callable=AsyncMock), \
             patch("app.api.routes.auth.get_platform_settings", new_callable=AsyncMock, return_value=mock_ps):
            
            req = MagicMock()
            resp = MagicMock()
            payload = GuestRecoverIn(recovery_code="K7M29XQP")

            with self.assertRaises(HTTPException) as ctx:
                await recover_guest(payload, req, resp, mock_db)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, {"code": "guest_mode_disabled"})

    async def test_b10_export_user_data_blocks_guest(self):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.email = "guest_abc123@guest.local"
        mock_user.is_guest = True

        with self.assertRaises(HTTPException) as ctx:
            await export_user_data(format="json", db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, {"code": "guest_feature_locked", "message": "Guest accounts cannot export data."})
        self.assertEqual(mock_db.add.call_count, 1)

if __name__ == "__main__":
    unittest.main()
