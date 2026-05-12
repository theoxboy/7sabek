from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.advisor_preview import AdvisorPreview
from app.models.user import User
from app.repositories.advisor.preview_repository import AdvisorPreviewRepository


@pytest.fixture()
async def db_session(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def _create_user(db: AsyncSession) -> User:
    user = User(
        email=f"persist-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
    )
    db.add(user)
    await db.flush()
    return user



def _preview(user_id, preview_id: str, *, profile_hash: str = "hash-1", expired: bool = False) -> AdvisorPreview:
    now = datetime.now(timezone.utc)
    return AdvisorPreview(
        preview_id=preview_id,
        user_id=user_id,
        status="active",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
        profile_hash=profile_hash,
        gating_hash=f"g-{profile_hash}",
        generated_at=now,
        expires_at=now - timedelta(minutes=5) if expired else now + timedelta(minutes=30),
        degraded_mode=False,
        can_recommend_confidently=True,
        recommended_proposal_id="balanced-1",
        warnings_snapshot=[],
        blocking_issues_snapshot=[],
        data_quality_snapshot={"completeness_score": 90, "reliability_score": 88},
        preview_payload={"preview_id": preview_id},
    )


@pytest.mark.asyncio
async def test_create_preview_persists_expected_fields(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    item = _preview(user.id, "p-int-1")
    await repo.create_preview(db_session, item)
    await db_session.commit()

    loaded = await repo.get_by_preview_id(db_session, "p-int-1")
    assert loaded is not None
    assert loaded.user_id == user.id
    assert loaded.status == "active"
    assert loaded.profile_hash == "hash-1"
    assert loaded.preview_payload["preview_id"] == "p-int-1"


@pytest.mark.asyncio
async def test_find_reusable_preview_returns_matching_active_preview(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    await repo.create_preview(db_session, _preview(user.id, "p-int-reuse", profile_hash="hash-ok"))
    await db_session.commit()

    found = await repo.find_reusable_preview(
        db_session,
        user.id,
        profile_hash="hash-ok",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
    )
    assert found is not None
    assert found.preview_id == "p-int-reuse"


@pytest.mark.asyncio
async def test_find_reusable_preview_ignores_expired_preview(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    await repo.create_preview(db_session, _preview(user.id, "p-int-exp", expired=True))
    await db_session.commit()

    found = await repo.find_reusable_preview(
        db_session,
        user.id,
        profile_hash="hash-1",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
    )
    assert found is None


@pytest.mark.asyncio
async def test_find_reusable_preview_ignores_hash_mismatch(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    await repo.create_preview(db_session, _preview(user.id, "p-int-hash", profile_hash="hash-a"))
    await db_session.commit()

    found = await repo.find_reusable_preview(
        db_session,
        user.id,
        profile_hash="hash-b",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
    )
    assert found is None


@pytest.mark.asyncio
async def test_mark_stale_for_user_marks_previous_active_previews(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    await repo.create_preview(db_session, _preview(user.id, "p-int-keep"))
    await repo.create_preview(db_session, _preview(user.id, "p-int-old-1"))
    await repo.create_preview(db_session, _preview(user.id, "p-int-old-2"))
    await db_session.commit()

    changed = await repo.mark_stale_for_user(
        db_session,
        user.id,
        superseded_by_preview_id="p-int-keep",
        exclude_preview_id="p-int-keep",
    )
    await db_session.commit()

    assert changed == 2
    assert (await repo.get_by_preview_id(db_session, "p-int-keep")).status == "active"
    assert (await repo.get_by_preview_id(db_session, "p-int-old-1")).status == "stale"
    assert (await repo.get_by_preview_id(db_session, "p-int-old-2")).superseded_by_preview_id == "p-int-keep"
