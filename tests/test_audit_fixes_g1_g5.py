import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from app.api.routes.dashboard import get_dashboard_alerts
from app.api.routes.mappings import list_mappings
from app.api.routes.distribution import create_distribution_rule, update_distribution_config
from app.schemas.distribution_rule import DistributionRuleCreate
from app.schemas.distribution import DistributionConfigIn, DistributionConfigItemOut


class TestAuditFixesG(unittest.IsolatedAsyncioTestCase):
    @patch("app.api.routes.dashboard.ensure_system_category_mappings", new_callable=AsyncMock)
    @patch("app.api.routes.dashboard._resolve_period_bounds", new_callable=AsyncMock)
    @patch("app.api.routes.dashboard._sweep_status_for_range", new_callable=AsyncMock)
    @patch("app.api.routes.dashboard.count_manual_unmapped_categories", new_callable=AsyncMock)
    @patch("app.api.routes.dashboard.build_sweep_bootstrap_status", new_callable=AsyncMock)
    async def test_g1_dashboard_alerts_skips_repair_for_guests(
        self, mock_bootstrap, mock_count, mock_sweep, mock_bounds, mock_mappings
    ):
        from datetime import date, timedelta
        from app.schemas.dashboard import SweepStatusOut
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_db = AsyncMock()
        today = date.today()
        mock_bounds.return_value = (today, today + timedelta(days=30))
        mock_sweep.return_value = SweepStatusOut(
            due=False,
            period_start=today,
            period_end=today + timedelta(days=30),
            income_declared=True,
            already_swept=False,
        )
        mock_count.return_value = 0
        mock_bootstrap.return_value = None
        mock_res = MagicMock()
        mock_res.all.return_value = []
        mock_db.execute.return_value = mock_res

        await get_dashboard_alerts(db=mock_db, current_user=mock_user)
        self.assertTrue(mock_mappings.called)
        self.assertEqual(mock_mappings.call_args[1].get("repair"), False)

    @patch("app.api.routes.mappings.ensure_system_category_mappings", new_callable=AsyncMock)
    async def test_g2_list_mappings_skips_repair_for_guests(self, mock_mappings):
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_db = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_res

        await list_mappings(db=mock_db, current_user=mock_user)
        self.assertTrue(mock_mappings.called)
        self.assertEqual(mock_mappings.call_args[1].get("repair"), False)

    async def test_g3_purge_guest_owned_rows_includes_email_preference(self):
        from app.api.routes.auth import _purge_guest_owned_rows
        from app.models.email_preference import EmailPreference

        mock_db = AsyncMock()
        user_id = uuid4()

        await _purge_guest_owned_rows(mock_db, user_id)
        executed_stmts = [str(call[0][0]) for call in mock_db.execute.call_args_list]
        email_pref_purged = any("email_preferences" in stmt for stmt in executed_stmts)
        self.assertTrue(email_pref_purged, "EmailPreference must be purged during guest data purge")

    async def test_g4_distribution_rules_and_config_block_goals_for_guests(self):
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.is_guest = True
        mock_db = AsyncMock()

        # create_distribution_rule targeting goal
        rule_payload = DistributionRuleCreate(
            target_type="goal",
            target_id=uuid4(),
            mode="fixed",
            amount=100.0,
            priority=1,
            rank=1,
            enabled=True,
            auto_apply_on_income=False,
        )
        with self.assertRaises(HTTPException) as ctx:
            await create_distribution_rule(rule_payload, db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")
        self.assertEqual(ctx.exception.detail.get("feature"), "goals")

        # update_distribution_config with goals
        config_payload = DistributionConfigIn(
            auto_enabled=True,
            envelopes=[],
            goals=[
                DistributionConfigItemOut(
                    target_id=uuid4(),
                    name="Goal 1",
                    mode="percent",
                    percent=10.0,
                    enabled=True,
                )
            ],
        )
        with self.assertRaises(HTTPException) as ctx:
            await update_distribution_config(config_payload, db=mock_db, current_user=mock_user)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail.get("code"), "guest_feature_locked")
        self.assertEqual(ctx.exception.detail.get("feature"), "goals")


if __name__ == "__main__":
    unittest.main()
