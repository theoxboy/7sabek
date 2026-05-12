from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.platform_settings import PlatformSettings
from app.models.user import User


def deletion_grace_deadline(
    user: User, settings: PlatformSettings
) -> datetime | None:
    if not user.deleted_at:
        return None
    return user.deleted_at + timedelta(
        days=max(1, settings.account_deletion_grace_days)
    )


def is_within_deletion_grace(
    user: User, settings: PlatformSettings
) -> bool:
    deadline = deletion_grace_deadline(user, settings)
    if not deadline:
        return False
    return datetime.now(timezone.utc) <= deadline


def build_deleted_account_message(
    user: User, settings: PlatformSettings
) -> str:
    deadline = deletion_grace_deadline(user, settings)
    support_email = settings.support_email or "support@floussy.online"
    if not deadline:
        return "Compte supprimé."
    now = datetime.now(timezone.utc)
    remaining = deadline - now
    if remaining.total_seconds() <= 0:
        return (
            "Compte supprimé. La période de récupération est expirée. "
            "Le compte sera supprimé définitivement."
        )
    total_seconds = int(remaining.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    days_label = "jour" if days == 1 else "jours"
    hours_label = "heure" if hours == 1 else "heures"
    return (
        "Compte supprimé. Pour récupérer ton compte, contacte le support à "
        f"{support_email} avec l'email du compte, ton nom complet et ton numéro "
        f"de téléphone. Il reste {days} {days_label} et {hours} {hours_label} "
        "avant la suppression définitive."
    )
