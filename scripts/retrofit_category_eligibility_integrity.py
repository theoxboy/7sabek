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
from app.services.category_mapping_integrity import ensure_system_category_mappings
from app.services.category_unmapped import count_manual_unmapped_categories


@dataclass
class RetrofitStats:
    users_scanned: int = 0
    users_with_unmapped_before: int = 0
    users_with_unmapped_after: int = 0
    total_unmapped_before: int = 0
    total_unmapped_after: int = 0
    mappings_created: int = 0
    mappings_updated: int = 0
    mappings_deleted: int = 0


async def retrofit_all_users(*, commit: bool) -> RetrofitStats:
    stats = RetrofitStats()
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session:
        users_result = await session.execute(
            select(User.id, User.email).order_by(User.created_at.asc())
        )
        users = users_result.all()
        stats.users_scanned = len(users)

        for user_id, email in users:
            unmapped_before = await count_manual_unmapped_categories(session, user_id)
            if unmapped_before > 0:
                stats.users_with_unmapped_before += 1
            stats.total_unmapped_before += unmapped_before

            created, updated, deleted = await ensure_system_category_mappings(
                session,
                user_id,
                repair=True,
            )
            stats.mappings_created += created
            stats.mappings_updated += updated
            stats.mappings_deleted += deleted

            unmapped_after = await count_manual_unmapped_categories(session, user_id)
            if unmapped_after > 0:
                stats.users_with_unmapped_after += 1
            stats.total_unmapped_after += unmapped_after

            print(
                "user="
                f"{email} "
                f"unmapped_before={unmapped_before} "
                f"unmapped_after={unmapped_after} "
                f"created={created} updated={updated} deleted={deleted}"
            )

        if commit:
            await session.commit()
        else:
            await session.rollback()

    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Retroactive category integrity for all users "
            "(eligibility-based seeding, pruning, and mapping reconciliation)."
        )
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Persist changes. Without this flag, run in dry-run mode (rollback).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = asyncio.run(retrofit_all_users(commit=args.commit))
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[{mode}] users_scanned={stats.users_scanned}")
    print(
        f"[{mode}] users_with_unmapped_before={stats.users_with_unmapped_before} "
        f"total_unmapped_before={stats.total_unmapped_before}"
    )
    print(
        f"[{mode}] users_with_unmapped_after={stats.users_with_unmapped_after} "
        f"total_unmapped_after={stats.total_unmapped_after}"
    )
    print(
        f"[{mode}] mappings_created={stats.mappings_created} "
        f"mappings_updated={stats.mappings_updated} "
        f"mappings_deleted={stats.mappings_deleted}"
    )


if __name__ == "__main__":
    main()
