from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import delete

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db.session import get_sessionmaker
from app.models import LoginThrottle, PlatformSettings, RateLimitBucket


async def disable_rate_limits() -> None:
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session:
        settings = await session.get(PlatformSettings, 1)
        if settings is None:
            settings = PlatformSettings(id=1)
            session.add(settings)
            await session.flush()

        settings.rate_limit_login_max = 0
        settings.rate_limit_login_window_minutes = 1
        settings.rate_limit_register_max = 0
        settings.rate_limit_register_window_minutes = 1
        settings.rate_limit_api_max = 0
        settings.rate_limit_api_window_minutes = 1

        await session.execute(delete(LoginThrottle))
        await session.execute(delete(RateLimitBucket))
        await session.commit()


def main() -> None:
    asyncio.run(disable_rate_limits())


if __name__ == "__main__":
    main()
