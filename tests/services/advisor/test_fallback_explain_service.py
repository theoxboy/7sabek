from __future__ import annotations

from app.services.advisor.fallback_explain_service import FallbackExplainService
from tests.fixtures.advisor.preview_fixtures import degraded_preview_output, normal_preview_output


def test_fallback_explain_enriches_titles_and_subtitles() -> None:
    service = FallbackExplainService()
    preview = normal_preview_output()

    enriched = service.enrich_preview(preview)

    assert len(enriched.proposals) > 0
    assert all((p.title_key or "") != "" for p in enriched.proposals)
    assert all((p.subtitle_key or "") != "" for p in enriched.proposals)


def test_fallback_explain_adds_degraded_signal() -> None:
    service = FallbackExplainService()
    preview = degraded_preview_output()

    enriched = service.enrich_preview(preview)

    assert all("Données partielles" in (p.subtitle_key or "") for p in enriched.proposals)
