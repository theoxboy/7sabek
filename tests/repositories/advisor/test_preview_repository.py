from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
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
        email=f"u-{uuid4()}@example.com",
        currency="MAD",
        sweep_interval_days=30,
        next_sweep_date=date.today(),
    )
    db.add(user)
    await db.flush()
    return user


def _build_preview(user_id, *, preview_id: str, profile_hash: str = "hash-a", active: bool = True, expired: bool = False) -> AdvisorPreview:
    now = datetime.now(timezone.utc)
    generated = now - timedelta(minutes=5)
    expires = now - timedelta(minutes=1) if expired else now + timedelta(minutes=30)
    return AdvisorPreview(
        preview_id=preview_id,
        user_id=user_id,
        status="active" if active else "stale",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
        profile_hash=profile_hash,
        gating_hash=f"g-{profile_hash}",
        generated_at=generated,
        expires_at=expires,
        degraded_mode=False,
        can_recommend_confidently=True,
        recommended_proposal_id="balanced-1",
        warnings_snapshot=[],
        blocking_issues_snapshot=[],
        data_quality_snapshot={"completeness_score": 90, "reliability_score": 85},
        preview_payload={"preview_id": preview_id},
    )


@pytest.mark.asyncio
async def test_create_preview_persists_expected_fields(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    preview = _build_preview(user.id, preview_id="pv-create")
    created = await repo.create_preview(db_session, preview)
    await db_session.commit()

    assert created.id is not None
    assert created.preview_id == "pv-create"
    assert created.status == "active"


@pytest.mark.asyncio
async def test_get_by_preview_id_returns_expected_preview(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    await repo.create_preview(db_session, _build_preview(user.id, preview_id="pv-get"))
    await db_session.commit()

    found = await repo.get_by_preview_id(db_session, "pv-get")
    assert found is not None
    assert found.preview_id == "pv-get"


@pytest.mark.asyncio
async def test_get_latest_active_for_user_returns_newest_non_expired(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    old_preview = _build_preview(user.id, preview_id="pv-old")
    old_preview.generated_at = datetime.now(timezone.utc) - timedelta(hours=2)

    latest_preview = _build_preview(user.id, preview_id="pv-latest")
    latest_preview.generated_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    await repo.create_preview(db_session, old_preview)
    await repo.create_preview(db_session, latest_preview)
    await db_session.commit()

    found = await repo.get_latest_active_for_user(db_session, user.id)
    assert found is not None
    assert found.preview_id == "pv-latest"


@pytest.mark.asyncio
async def test_find_reusable_preview_returns_matching_active_preview(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    preview = _build_preview(user.id, preview_id="pv-reuse", profile_hash="hash-ok")
    await repo.create_preview(db_session, preview)
    await db_session.commit()

    found = await repo.find_reusable_preview(
        db_session,
        user.id,
        profile_hash="hash-ok",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
    )
    assert found is not None
    assert found.preview_id == "pv-reuse"


@pytest.mark.asyncio
async def test_find_reusable_preview_ignores_expired_preview(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    expired = _build_preview(user.id, preview_id="pv-expired", expired=True)
    await repo.create_preview(db_session, expired)
    await db_session.commit()

    found = await repo.find_reusable_preview(
        db_session,
        user.id,
        profile_hash="hash-a",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
    )
    assert found is None


@pytest.mark.asyncio
async def test_find_reusable_preview_ignores_hash_mismatch(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    preview = _build_preview(user.id, preview_id="pv-hash", profile_hash="hash-real")
    await repo.create_preview(db_session, preview)
    await db_session.commit()

    found = await repo.find_reusable_preview(
        db_session,
        user.id,
        profile_hash="hash-other",
        engine_version="advisor-engine-v1",
        proposal_contract_version="AdvisorPreviewResponseV1",
    )
    assert found is None


@pytest.mark.asyncio
async def test_mark_stale_for_user_updates_only_active_previews(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    keep = _build_preview(user.id, preview_id="pv-keep")
    stale_1 = _build_preview(user.id, preview_id="pv-stale-1")
    stale_2 = _build_preview(user.id, preview_id="pv-stale-2")

    await repo.create_preview(db_session, keep)
    await repo.create_preview(db_session, stale_1)
    await repo.create_preview(db_session, stale_2)
    await db_session.commit()

    changed = await repo.mark_stale_for_user(
        db_session,
        user.id,
        superseded_by_preview_id="pv-keep",
        exclude_preview_id="pv-keep",
    )
    await db_session.commit()

    assert changed == 2

    keep_db = await repo.get_by_preview_id(db_session, "pv-keep")
    stale_1_db = await repo.get_by_preview_id(db_session, "pv-stale-1")
    stale_2_db = await repo.get_by_preview_id(db_session, "pv-stale-2")

    assert keep_db is not None and keep_db.status == "active"
    assert stale_1_db is not None and stale_1_db.status == "stale"
    assert stale_2_db is not None and stale_2_db.status == "stale"
    assert stale_1_db.superseded_by_preview_id == "pv-keep"
    assert stale_2_db.superseded_by_preview_id == "pv-keep"


@pytest.mark.asyncio
async def test_unique_preview_id_is_enforced(db_session: AsyncSession) -> None:
    repo = AdvisorPreviewRepository()
    user = await _create_user(db_session)

    await repo.create_preview(db_session, _build_preview(user.id, preview_id="pv-uniq"))
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await repo.create_preview(db_session, _build_preview(user.id, preview_id="pv-uniq"))
        await db_session.flush()
