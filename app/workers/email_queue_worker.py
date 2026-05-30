from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.email_center import process_delivery_batch

logger = logging.getLogger("app.email_queue_worker")


async def _run_once(db: AsyncSession) -> None:
    settings = get_settings()
    if not settings.email_center_delivery_queue_enabled:
        return
    if settings.email_center_kill_switch:
        return
    limit = max(1, int(settings.email_center_queue_batch_size))
    result = await process_delivery_batch(db, limit)
    logger.info(
        "event=email_queue_batch attempted=%s sent=%s failed=%s retry=%s remaining=%s",
        result.get("attempted", 0),
        result.get("sent", 0),
        result.get("failed", 0),
        result.get("retry", 0),
        result.get("remaining_pending", 0),
    )


async def main() -> None:
    while True:
        async with SessionLocal() as db:
            await _run_once(db)
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
