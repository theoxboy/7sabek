from fastapi import APIRouter

from app.api.routes.categories import router as categories_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.envelopes import router as envelopes_router
from app.api.routes.goals import router as goals_router
from app.api.routes.health import router as health_router
from app.api.routes.income_reminders import router as income_reminders_router
from app.api.routes.logs import router as logs_router
from app.api.routes.gamification import router as gamification_router
from app.api.routes.leaderboard import router as leaderboard_router
from app.api.routes.mappings import router as mappings_router
from app.api.routes.auth import router as auth_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.admin_activity import router as admin_activity_router
from app.api.routes.admin_backups import router as admin_backups_router
from app.api.routes.admin_settings import router as admin_settings_router
from app.api.routes.public import router as public_router
from app.api.routes.distribution import router as distribution_router
from app.api.routes.reports import router as reports_router
from app.api.routes.sweeps import router as sweeps_router
from app.api.routes.transactions import router as transactions_router
from app.api.routes.users import router as users_router
from app.api.routes.advisor import router as advisor_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(public_router, tags=["public"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(analytics_router, tags=["analytics"])
api_router.include_router(admin_activity_router, tags=["admin-activity"])
api_router.include_router(admin_backups_router, tags=["admin-backups"])
api_router.include_router(admin_settings_router, tags=["admin-settings"])
api_router.include_router(users_router, tags=["users"])
api_router.include_router(envelopes_router, tags=["envelopes"])
api_router.include_router(goals_router, tags=["goals"])
api_router.include_router(income_reminders_router, tags=["income-reminders"])
api_router.include_router(logs_router, tags=["logs"])
api_router.include_router(gamification_router, tags=["gamification"])
api_router.include_router(leaderboard_router, tags=["leaderboard"])
api_router.include_router(categories_router, tags=["categories"])
api_router.include_router(mappings_router, tags=["mappings"])
api_router.include_router(transactions_router, tags=["transactions"])
api_router.include_router(sweeps_router, tags=["sweeps"])
api_router.include_router(dashboard_router, tags=["dashboard"])
api_router.include_router(distribution_router, tags=["distribution"])
api_router.include_router(reports_router, tags=["reports"])
api_router.include_router(advisor_router, tags=["advisor"])
