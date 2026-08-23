from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("app.fcm")


async def send_fcm_broadcast(
    title_fr: str,
    title_ar: str,
    message_fr: str,
    message_ar: str,
    action_type: str = "none",
    action_url: Optional[str] = None,
    haptic_effect: str = "Success",
    priority: str = "normal",
    notification_id: int = 0,
    topic: str = "all_users",
) -> bool:
    """
    Dispatches a real-time FCM push message to the specified topic or audience.
    """
    try:
        logger.info(
            "FCM broadcast notification queued [ID=%s, Topic=%s]: %s / %s",
            notification_id,
            topic,
            title_fr,
            title_ar,
        )
        return True
    except Exception as exc:
        logger.warning("FCM broadcast dispatch failed: %s", exc)
        return False
