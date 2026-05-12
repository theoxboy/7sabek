from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_preview import AdvisorPreview


class AdvisorPreviewRepository:
    """Persistence access for advisor previews (Sprint 1 preview-only)."""

    async def create_preview(
        self,
        db: AsyncSession,
        preview: AdvisorPreview,
    ) -> AdvisorPreview:
        db.add(preview)
        await db.flush()
        return preview

    async def get_by_preview_id(
        self,
        db: AsyncSession,
        preview_id: str,
    ) -> Optional[AdvisorPreview]:
        result = await db.execute(
            select(AdvisorPreview).where(AdvisorPreview.preview_id == preview_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_active_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
        now: datetime | None = None,
    ) -> Optional[AdvisorPreview]:
        ref_now = now or datetime.now(timezone.utc)
        result = await db.execute(
            select(AdvisorPreview)
            .where(
                AdvisorPreview.user_id == user_id,
                AdvisorPreview.status == "active",
                AdvisorPreview.expires_at > ref_now,
            )
            .order_by(AdvisorPreview.generated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_reusable_preview(
        self,
        db: AsyncSession,
        user_id: UUID,
        profile_hash: str,
        engine_version: str,
        proposal_contract_version: str,
        now: datetime | None = None,
    ) -> Optional[AdvisorPreview]:
        ref_now = now or datetime.now(timezone.utc)
        stmt: Select[tuple[AdvisorPreview]] = (
            select(AdvisorPreview)
            .where(
                AdvisorPreview.user_id == user_id,
                AdvisorPreview.status == "active",
                AdvisorPreview.expires_at > ref_now,
                AdvisorPreview.profile_hash == profile_hash,
                AdvisorPreview.engine_version == engine_version,
                AdvisorPreview.proposal_contract_version == proposal_contract_version,
            )
            .order_by(AdvisorPreview.generated_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_stale_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
        superseded_by_preview_id: str,
        *,
        exclude_preview_id: str | None = None,
    ) -> int:
        stmt = (
            update(AdvisorPreview)
            .where(
                AdvisorPreview.user_id == user_id,
                AdvisorPreview.status == "active",
            )
            .values(
                status="stale",
                superseded_by_preview_id=superseded_by_preview_id,
            )
        )
        if exclude_preview_id is not None:
            stmt = stmt.where(AdvisorPreview.preview_id != exclude_preview_id)

        result = await db.execute(stmt)
        await db.flush()
        return int(result.rowcount or 0)

    # Backward-compatible wrappers used by existing services.
    async def create(self, db: AsyncSession, entity: AdvisorPreview) -> AdvisorPreview:
        return await self.create_preview(db, entity)

    async def get_latest_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> Optional[AdvisorPreview]:
        result = await db.execute(
            select(AdvisorPreview)
            .where(AdvisorPreview.user_id == user_id)
            .order_by(AdvisorPreview.generated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_stale(
        self,
        db: AsyncSession,
        entity: AdvisorPreview,
        superseded_by_preview_id: str | None = None,
    ) -> None:
        entity.status = "stale"
        entity.superseded_by_preview_id = superseded_by_preview_id
        await db.flush()
