from __future__ import annotations

import json
from datetime import date, datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool


def make_alembic_config(database_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def alembic_upgrade(cfg: Config, revision: str = "head") -> None:
    command.upgrade(cfg, revision)


def alembic_downgrade(cfg: Config, revision: str) -> None:
    command.downgrade(cfg, revision)


def make_async_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, poolclass=NullPool)


async def table_exists(engine: AsyncEngine, table_name: str) -> bool:
    async with engine.begin() as conn:
        return await conn.run_sync(lambda sc: sa.inspect(sc).has_table(table_name))


async def get_columns(engine: AsyncEngine, table_name: str) -> dict[str, dict]:
    async with engine.begin() as conn:
        return await conn.run_sync(
            lambda sc: {c["name"]: c for c in sa.inspect(sc).get_columns(table_name)}
        )


async def get_indexes(engine: AsyncEngine, table_name: str) -> dict[str, dict]:
    async with engine.begin() as conn:
        return await conn.run_sync(
            lambda sc: {i["name"]: i for i in sa.inspect(sc).get_indexes(table_name)}
        )


async def get_unique_constraints(engine: AsyncEngine, table_name: str) -> dict[str, dict]:
    async with engine.begin() as conn:
        return await conn.run_sync(
            lambda sc: {
                u["name"]: u for u in sa.inspect(sc).get_unique_constraints(table_name)
            }
        )


async def insert_minimal_preview_row(engine: AsyncEngine, *, preview_id: str | None = None) -> str:
    resolved_preview_id = preview_id or f"pv-{uuid4()}"
    user_id = str(uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now.replace(microsecond=0)
    generated_at = now.replace(microsecond=0)

    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                """
                INSERT INTO users (
                  id, email, currency, sweep_interval_days, next_sweep_date,
                  auto_distribution_enabled
                ) VALUES (
                  :id, :email, :currency, :sweep_interval_days, :next_sweep_date,
                  :auto_distribution_enabled
                )
                """
            ),
            {
                "id": user_id,
                "email": f"migration-{uuid4()}@example.com",
                "currency": "MAD",
                "sweep_interval_days": 30,
                "next_sweep_date": date.today(),
                "auto_distribution_enabled": False,
            },
        )

        await conn.execute(
            sa.text(
                """
                INSERT INTO advisor_previews (
                  preview_id,
                  user_id,
                  status,
                  engine_version,
                  proposal_contract_version,
                  profile_hash,
                  gating_hash,
                  generated_at,
                  expires_at,
                  degraded_mode,
                  can_recommend_confidently,
                  recommended_proposal_id,
                  warnings_snapshot,
                  blocking_issues_snapshot,
                  data_quality_snapshot,
                  preview_payload,
                  superseded_by_preview_id
                ) VALUES (
                  :preview_id,
                  :user_id,
                  :status,
                  :engine_version,
                  :proposal_contract_version,
                  :profile_hash,
                  :gating_hash,
                  :generated_at,
                  :expires_at,
                  :degraded_mode,
                  :can_recommend_confidently,
                  :recommended_proposal_id,
                  CAST(:warnings_snapshot AS jsonb),
                  CAST(:blocking_issues_snapshot AS jsonb),
                  CAST(:data_quality_snapshot AS jsonb),
                  CAST(:preview_payload AS jsonb),
                  :superseded_by_preview_id
                )
                """
            ),
            {
                "preview_id": resolved_preview_id,
                "user_id": user_id,
                "status": "active",
                "engine_version": "advisor-engine-v1",
                "proposal_contract_version": "AdvisorPreviewResponseV1",
                "profile_hash": "hash-profile-1",
                "gating_hash": "hash-gating-1",
                "generated_at": generated_at,
                "expires_at": expires_at,
                "degraded_mode": False,
                "can_recommend_confidently": True,
                "recommended_proposal_id": "balanced-1",
                "warnings_snapshot": json.dumps([]),
                "blocking_issues_snapshot": json.dumps([]),
                "data_quality_snapshot": json.dumps(
                    {"completeness_score": 90, "reliability_score": 85}
                ),
                "preview_payload": json.dumps({"preview_id": resolved_preview_id}),
                "superseded_by_preview_id": None,
            },
        )

    return resolved_preview_id
