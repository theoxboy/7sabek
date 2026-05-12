from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

from sqlalchemy import select

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models import User


async def create_or_update_superadmin(
    email: str,
    password: str,
    currency: str,
    sweep_interval_days: int,
) -> None:
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        next_sweep_date = date.today() + timedelta(days=sweep_interval_days)

        if user is None:
            user = User(
                email=email,
                password_hash=hash_password(password),
                currency=currency,
                sweep_interval_days=sweep_interval_days,
                next_sweep_date=next_sweep_date,
                role="superadmin",
            )
            session.add(user)
            await session.commit()
            print(f"Superadmin créé: {email}")
            return

        user.password_hash = hash_password(password)
        user.currency = currency
        user.sweep_interval_days = sweep_interval_days
        user.next_sweep_date = next_sweep_date
        user.role = "superadmin"
        await session.commit()
        print(f"Superadmin mis à jour: {email}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Créer ou mettre à jour un compte superadmin."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--currency", default="MAD")
    parser.add_argument("--sweep-interval-days", type=int, default=30)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        create_or_update_superadmin(
            email=args.email,
            password=args.password,
            currency=args.currency,
            sweep_interval_days=args.sweep_interval_days,
        )
    )


if __name__ == "__main__":
    main()
