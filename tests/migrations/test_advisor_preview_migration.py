from __future__ import annotations

import os

import pytest
from sqlalchemy.exc import IntegrityError

from tests.migrations.helpers import (
    alembic_downgrade,
    alembic_upgrade,
    get_columns,
    get_indexes,
    get_unique_constraints,
    insert_minimal_preview_row,
    make_alembic_config,
    make_async_engine,
    table_exists,
)

TARGET_TABLE = "advisor_previews"
PREVIOUS_REVISION = "20260403_platform_ai_gateways"


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL must be set for migration tests")
    return value


@pytest.fixture(scope="session")
def alembic_cfg(database_url: str):
    return make_alembic_config(database_url)


@pytest.fixture(scope="session", autouse=True)
def apply_migrations() -> None:
    # Neutralise root tests/conftest.py autouse migration fixture for this module.
    return None


@pytest.fixture(autouse=True)
def clean_db() -> None:
    # Migration tests control DB lifecycle explicitly; no truncate fixture here.
    return None


@pytest.mark.asyncio
async def test_migration_upgrade_creates_advisor_previews_table(alembic_cfg, database_url: str) -> None:
    alembic_downgrade(alembic_cfg, PREVIOUS_REVISION)
    alembic_upgrade(alembic_cfg, "head")

    engine = make_async_engine(database_url)
    try:
        assert await table_exists(engine, TARGET_TABLE) is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_upgrade_has_required_columns(alembic_cfg, database_url: str) -> None:
    alembic_downgrade(alembic_cfg, PREVIOUS_REVISION)
    alembic_upgrade(alembic_cfg, "head")

    engine = make_async_engine(database_url)
    try:
        columns = await get_columns(engine, TARGET_TABLE)
    finally:
        await engine.dispose()

    required = {
        "id",
        "preview_id",
        "user_id",
        "status",
        "engine_version",
        "proposal_contract_version",
        "profile_hash",
        "gating_hash",
        "generated_at",
        "expires_at",
        "degraded_mode",
        "can_recommend_confidently",
        "recommended_proposal_id",
        "warnings_snapshot",
        "blocking_issues_snapshot",
        "data_quality_snapshot",
        "preview_payload",
        "superseded_by_preview_id",
    }
    assert required.issubset(set(columns.keys()))


@pytest.mark.asyncio
async def test_migration_upgrade_has_required_indexes(alembic_cfg, database_url: str) -> None:
    alembic_downgrade(alembic_cfg, PREVIOUS_REVISION)
    alembic_upgrade(alembic_cfg, "head")

    engine = make_async_engine(database_url)
    try:
        indexes = await get_indexes(engine, TARGET_TABLE)
        uniques = await get_unique_constraints(engine, TARGET_TABLE)
    finally:
        await engine.dispose()

    assert "ix_advisor_previews_user_id" in indexes
    assert "ix_advisor_previews_status" in indexes
    assert "ix_advisor_previews_expires_at" in indexes
    assert "ix_advisor_previews_user_generated" in indexes
    assert "ix_advisor_previews_user_status" in indexes
    assert "uq_advisor_previews_preview_id" in uniques


@pytest.mark.asyncio
async def test_migration_upgrade_allows_minimal_preview_insert(alembic_cfg, database_url: str) -> None:
    alembic_downgrade(alembic_cfg, PREVIOUS_REVISION)
    alembic_upgrade(alembic_cfg, "head")

    engine = make_async_engine(database_url)
    try:
        preview_id = await insert_minimal_preview_row(engine)
        assert preview_id.startswith("pv-")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_enforces_unique_preview_id(alembic_cfg, database_url: str) -> None:
    alembic_downgrade(alembic_cfg, PREVIOUS_REVISION)
    alembic_upgrade(alembic_cfg, "head")

    engine = make_async_engine(database_url)
    try:
        preview_id = "pv-dup-1"
        await insert_minimal_preview_row(engine, preview_id=preview_id)
        with pytest.raises(IntegrityError):
            await insert_minimal_preview_row(engine, preview_id=preview_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_downgrade_drops_advisor_previews_table(alembic_cfg, database_url: str) -> None:
    alembic_upgrade(alembic_cfg, "head")
    alembic_downgrade(alembic_cfg, PREVIOUS_REVISION)

    engine = make_async_engine(database_url)
    try:
        assert await table_exists(engine, TARGET_TABLE) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_round_trip_succeeds(alembic_cfg, database_url: str) -> None:
    alembic_upgrade(alembic_cfg, "head")
    alembic_downgrade(alembic_cfg, PREVIOUS_REVISION)
    alembic_upgrade(alembic_cfg, "head")

    engine = make_async_engine(database_url)
    try:
        assert await table_exists(engine, TARGET_TABLE) is True
        preview_id = await insert_minimal_preview_row(engine)
        assert preview_id
    finally:
        await engine.dispose()
