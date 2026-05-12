from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

from sqlalchemy import select

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db.session import get_sessionmaker
from app.models import User
from app.services.category_auto_mapping import reconcile_category_mappings_for_user


@dataclass
class RetrofitStats:
    users_scanned: int = 0
    mappings_created: int = 0
    mappings_updated: int = 0
    mappings_deleted: int = 0


async def retrofit_all_users(*, commit: bool) -> RetrofitStats:
    stats = RetrofitStats()
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session:
        users_result = await session.execute(select(User.id, User.email).order_by(User.created_at.asc()))
        users = users_result.all()
        stats.users_scanned = len(users)

        for user_id, email in users:
            created, updated, deleted = await reconcile_category_mappings_for_user(
                session,
                user_id,
                allow_group_envelope_creation=True,
            )
            stats.mappings_created += created
            stats.mappings_updated += updated
            stats.mappings_deleted += deleted
            print(
                f"user={email} created={created} updated={updated} deleted={deleted}"
            )

        if commit:
            await session.commit()
        else:
            await session.rollback()
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrofit category-envelope mappings for all existing users using group-first logic."
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist changes. Without this flag, run as dry-run and rollback.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = asyncio.run(retrofit_all_users(commit=args.commit))
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode}] users_scanned={stats.users_scanned}")
    print(f"[{mode}] mappings_created={stats.mappings_created}")
    print(f"[{mode}] mappings_updated={stats.mappings_updated}")
    print(f"[{mode}] mappings_deleted={stats.mappings_deleted}")


if __name__ == "__main__":
    main()

