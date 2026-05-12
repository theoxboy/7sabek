from __future__ import annotations

from app.models import Envelope
from app.services.category_catalog import category_key_from_name
from app.services.category_auto_mapping import suggest_envelope_for_category_name


def _env(name: str) -> Envelope:
    return Envelope(
        name=name,
        rollover_enabled=False,
        is_default_savings=False,
        is_cash=False,
        is_goal=False,
        deletable=True,
    )


def test_suggest_envelope_for_phone_prefers_bills() -> None:
    envelopes = [_env("Factures"), _env("Loisirs"), _env("Nourriture")]
    selected = suggest_envelope_for_category_name("phone", envelopes)
    assert selected is not None
    assert selected.name == "Factures"


def test_suggest_envelope_for_family_prefers_family_envelope() -> None:
    envelopes = [_env("Aide famille"), _env("التوازن"), _env("Nourriture")]
    selected = suggest_envelope_for_category_name("children_school", envelopes)
    assert selected is not None
    assert selected.name == "Aide famille"


def test_suggest_envelope_for_family_without_family_returns_none() -> None:
    envelopes = [_env("التوازن"), _env("Nourriture"), _env("Loisirs")]
    selected = suggest_envelope_for_category_name("children_school", envelopes)
    assert selected is None


def test_category_alias_sante_is_canonical_health_generic() -> None:
    assert category_key_from_name("sante") == "health_generic"
    assert category_key_from_name("santé") == "health_generic"


def test_transport_generic_does_not_map_to_specialized_auto_envelope() -> None:
    envelopes = [_env("Assurance auto"), _env("Carburant"), _env("Transport")]
    selected = suggest_envelope_for_category_name("transport_generic", envelopes)
    assert selected is not None
    assert selected.name == "Transport"


def test_health_generic_prefers_health_over_food() -> None:
    envelopes = [_env("Nourriture"), _env("الصحة"), _env("Factures")]
    selected = suggest_envelope_for_category_name("health_generic", envelopes)
    assert selected is not None
    assert selected.name == "الصحة"
