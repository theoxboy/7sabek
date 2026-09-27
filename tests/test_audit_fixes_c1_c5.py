import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from app.api.routes.users import get_admin_top_clients, reset_user_data, export_user_data
from app.api.routes.leaderboard import build_leaderboard
from app.models import UserGamification
from app.services.email_center import search_users_for_email_center, _fetch_audience_users


class TestAuditFixesC(unittest.IsolatedAsyncioTestCase):
    async def test_c1_email_center_filters_guests(self):
        mock_db = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_res

        # Test search_users_for_email_center
        await search_users_for_email_center(mock_db, query="test")
        stmt_search = mock_db.execute.call_args[0][0]
        compiled_search = str(stmt_search)
        self.assertIn("users.is_guest IS false", compiled_search)
        self.assertIn("users.deleted_at IS NULL", compiled_search)

        # Test _fetch_audience_users
        warnings = []
        await _fetch_audience_users(mock_db, audience_type="all_users", warnings=warnings)
        stmt_fetch = mock_db.execute.call_args[0][0]
        compiled_fetch = str(stmt_fetch)
        self.assertIn("users.is_guest IS false", compiled_fetch)
        self.assertIn("users.deleted_at IS NULL", compiled_fetch)

    async def test_c2_get_admin_top_clients_filters_guests_and_deleted(self):
        mock_db = AsyncMock()
        mock_res = MagicMock()
        mock_res.all.return_value = []
        mock_db.execute.return_value = mock_res

        mock_admin = MagicMock()
        mock_admin.role = "superadmin"

        await get_admin_top_clients(limit=5, db=mock_db, current_user=mock_admin)
        # There should be 2 execute calls (main query and fallback query because rows was empty)
        self.assertEqual(mock_db.execute.call_count, 2)
        
        main_query = str(mock_db.execute.call_args_list[0][0][0])
        self.assertIn("users.is_guest IS false", main_query)
        self.assertIn("users.deleted_at IS NULL", main_query)

        fallback_query = str(mock_db.execute.call_args_list[1][0][0])
        self.assertIn("users.is_guest IS false", fallback_query)
        self.assertIn("users.deleted_at IS NULL", fallback_query)

    async def test_c3_reset_user_data_blocks_guest(self):
        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True

        with self.assertRaises(HTTPException) as ctx:
            await reset_user_data(db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail.get("code"), "use_guest_delete")

    async def test_c4_leaderboard_bypasses_gamification_for_guests(self):
        mock_db = AsyncMock()
        mock_res = MagicMock()
        mock_res.all.return_value = []
        mock_db.execute.return_value = mock_res

        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_user.leaderboard_name = "GuestTester"

        with patch("app.api.routes.leaderboard.get_or_create_gamification", new_callable=AsyncMock) as mock_get_gamif:
            result = await build_leaderboard("weekly", UserGamification.points_weekly, mock_db, mock_user)
            mock_get_gamif.assert_not_called()
            self.assertFalse(result.opt_in)
            self.assertIsNone(result.user_rank)
            self.assertIsNone(result.user_points)

    async def test_c5_export_user_data_blocks_guest_and_emits_event(self):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True

        with self.assertRaises(HTTPException) as ctx:
            await export_user_data(format="json", db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, {"code": "guest_feature_locked", "message": "Guest accounts cannot export data."})
        self.assertEqual(mock_db.add.call_count, 1)
        added_obj = mock_db.add.call_args[0][0]
        self.assertEqual(added_obj.name, "guest_wall_hit")
        self.assertEqual(added_obj.meta.get("wall"), "export")


if __name__ == "__main__":
    unittest.main()
