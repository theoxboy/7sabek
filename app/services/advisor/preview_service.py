from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advisor_preview import AdvisorPreview
from app.models.user import User
from app.repositories.advisor.preview_repository import AdvisorPreviewRepository
from app.schemas.advisor.api import AdvisorPreviewEnvelopeOut
from app.schemas.advisor.contracts import AdvisorPreviewResponseV1
from app.services.advisor.fallback_explain_service import FallbackExplainService
from app.services.advisor.gating_service import GatingService
from app.services.advisor.normalizer_service import NormalizerService
from app.services.advisor.proposal_engine_service import ProposalEngineService


class AdvisorPreviewService:
    """Orchestrates profile + gating + engine + fallback explain + persistence."""

    preview_ttl_minutes = 30
    proposal_contract_version = "AdvisorPreviewResponseV1"
    logger = logging.getLogger("app.advisor.preview")

    def __init__(
        self,
        normalizer: NormalizerService,
        gating: GatingService,
        engine: ProposalEngineService,
        previews: AdvisorPreviewRepository,
        fallback_explain: FallbackExplainService | None = None,
    ) -> None:
        self.normalizer = normalizer
        self.gating = gating
        self.engine = engine
        self.previews = previews
        self.fallback_explain = fallback_explain or FallbackExplainService()

    async def generate(
        self,
        db: AsyncSession,
        user: User,
        source: str,
        force_regenerate: bool = False,
    ) -> AdvisorPreviewEnvelopeOut:
        try:
            profile = await self.normalizer.build_profile(db, user)
        except Exception as exc:
            raise RuntimeError("ADVISOR_NORMALIZER_FAILED") from exc

        debug_notes = self._notes_as_dict(profile.data_quality.notes if profile.data_quality else [])
        self.logger.info(
            "advisor_preview_input user_id=%s payload_top_keys=%s income_key=%s income_raw=%s income_frequency=%s cycle_days=%s monthly_income_total=%s cycle_income_total=%s available_now_amount=%s",
            user.id,
            debug_notes.get("payload_top_keys"),
            debug_notes.get("income_key"),
            debug_notes.get("income_raw"),
            debug_notes.get("income_frequency"),
            profile.metadata.cycle_days,
            profile.income_profile.monthly_income_total,
            profile.income_profile.cycle_income_total,
            profile.current_cash_snapshot.available_now_amount,
        )

        profile_hash = self._sha(profile.model_dump(mode="json"))
        now = datetime.now(timezone.utc)

        if not force_regenerate:
            reusable = await self.previews.find_reusable_preview(
                db=db,
                user_id=user.id,
                profile_hash=profile_hash,
                engine_version=self.engine.engine_version,
                proposal_contract_version=self.proposal_contract_version,
                now=now,
            )
            if reusable is not None:
                return self._envelope_from_entity(reusable)

        try:
            gating_out = self.gating.evaluate(profile)
        except Exception as exc:
            raise RuntimeError("ADVISOR_GATING_FAILED") from exc

        try:
            preview = self.engine.generate_preview(profile, gating_out)
        except Exception as exc:
            raise RuntimeError("ADVISOR_ENGINE_FAILED") from exc

        try:
            preview = self.fallback_explain.enrich_preview(preview)
        except Exception:
            # Non-blocking by design; keep base preview if fallback explain fails.
            pass

        if self._advisor_debug_enabled():
            debug_warnings = [
                f"DEBUG_income_key={debug_notes.get('income_key', 'none')}",
                f"DEBUG_income_raw={debug_notes.get('income_raw', 'none')}",
                f"DEBUG_income_frequency={debug_notes.get('income_frequency', 'none')}",
                f"DEBUG_income_scope={debug_notes.get('income_scope', 'none')}",
                f"DEBUG_onboarding_record_id={debug_notes.get('onboarding_record_id', 'none')}",
                f"DEBUG_cycle_days={profile.metadata.cycle_days}",
                f"DEBUG_monthly_income_total={profile.income_profile.monthly_income_total}",
                f"DEBUG_cycle_income_total={profile.income_profile.cycle_income_total}",
            ]
            preview.warnings = [*preview.warnings, *debug_warnings]

        gating_hash = self._sha(gating_out.model_dump(mode="json"))
        expires_at = now + timedelta(minutes=self.preview_ttl_minutes)

        entity = AdvisorPreview(
            preview_id=str(preview.preview_id),
            user_id=user.id,
            status="active",
            engine_version=preview.engine_version,
            proposal_contract_version=self.proposal_contract_version,
            profile_hash=profile_hash,
            gating_hash=gating_hash,
            generated_at=preview.generated_at,
            expires_at=expires_at,
            degraded_mode=preview.degraded_mode,
            can_recommend_confidently=preview.can_recommend_confidently,
            recommended_proposal_id=preview.recommended_proposal_id,
            warnings_snapshot=preview.warnings,
            blocking_issues_snapshot=preview.blocking_issues,
            data_quality_snapshot=preview.data_quality_summary.model_dump(mode="json"),
            preview_payload=preview.model_dump(mode="json"),
        )

        try:
            await self.previews.create_preview(db, entity)
            await self.previews.mark_stale_for_user(
                db,
                user_id=user.id,
                superseded_by_preview_id=entity.preview_id,
                exclude_preview_id=entity.preview_id,
            )
        except Exception as exc:
            raise RuntimeError("ADVISOR_PREVIEW_PERSIST_FAILED") from exc

        return AdvisorPreviewEnvelopeOut(
            preview_id=UUID(entity.preview_id),
            advisor_preview=preview,
            freshness={
                "profile_hash": entity.profile_hash,
                "engine_version": entity.engine_version,
                "generated_at": entity.generated_at,
                "expires_at": entity.expires_at,
            },
        )

    def _envelope_from_entity(self, entity: AdvisorPreview) -> AdvisorPreviewEnvelopeOut:
        parsed_preview = AdvisorPreviewResponseV1.model_validate(entity.preview_payload)
        return AdvisorPreviewEnvelopeOut(
            preview_id=UUID(entity.preview_id),
            advisor_preview=parsed_preview,
            freshness={
                "profile_hash": entity.profile_hash,
                "engine_version": entity.engine_version,
                "generated_at": entity.generated_at,
                "expires_at": entity.expires_at,
            },
        )

    def _sha(self, payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _notes_as_dict(self, notes: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for note in notes:
            if ":" not in note:
                continue
            key, value = note.split(":", 1)
            out[key] = value
        return out

    def _advisor_debug_enabled(self) -> bool:
        return os.getenv("ADVISOR_DEBUG", "").strip() == "1"
