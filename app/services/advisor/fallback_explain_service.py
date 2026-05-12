from __future__ import annotations

from app.schemas.advisor.contracts import (
    AdvisorPreviewResponseV1,
    MainPriorityEnum,
    ProposalTypeEnum,
)


class FallbackExplainService:
    """Deterministic local explain layer used in Sprint 1 (no AI provider)."""

    _TITLES: dict[ProposalTypeEnum, str] = {
        ProposalTypeEnum.safe: "Plan safe",
        ProposalTypeEnum.balanced: "Plan balanced",
        ProposalTypeEnum.debt_first: "Plan debt-first",
        ProposalTypeEnum.goal_first: "Plan goal-first",
        ProposalTypeEnum.stability_first: "Plan stability-first",
        ProposalTypeEnum.catch_up: "Plan catch-up",
    }

    def enrich_preview(self, preview: AdvisorPreviewResponseV1, locale: str = "fr") -> AdvisorPreviewResponseV1:
        title_suffix = "(mode prudent)" if preview.degraded_mode else ""
        for proposal in preview.proposals:
            base_title = self._TITLES.get(proposal.proposal_type, "Plan advisor")
            proposal.title_key = f"{base_title} {title_suffix}".strip()

            if proposal.recommendation_layer.main_priority == MainPriorityEnum.debt:
                subtitle = "Priorité dette"
            elif proposal.recommendation_layer.main_priority == MainPriorityEnum.goals:
                subtitle = "Priorité objectifs"
            elif proposal.recommendation_layer.main_priority == MainPriorityEnum.recovery:
                subtitle = "Priorité récupération"
            else:
                subtitle = "Priorité stabilité"

            if preview.degraded_mode:
                subtitle = f"{subtitle} • Données partielles"

            proposal.subtitle_key = subtitle

            if "fallback_local" not in proposal.review_details.assumptions_used:
                proposal.review_details.assumptions_used.append("fallback_local")

            if preview.degraded_mode and "data_quality_limit" not in proposal.recommendation_layer.risk_tags:
                proposal.recommendation_layer.risk_tags.append("data_quality_limit")

        return preview
