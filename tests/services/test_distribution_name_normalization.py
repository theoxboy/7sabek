from __future__ import annotations

from app.services.distribution_name_normalization import (
    distribution_name_equivalent_key,
)


def test_distribution_name_equivalent_key_maps_multilingual_variants() -> None:
    assert distribution_name_equivalent_key("Nourriture") == "food"
    assert distribution_name_equivalent_key("الماكلة") == "food"
    assert distribution_name_equivalent_key("Santé") == "health"
    assert distribution_name_equivalent_key("الصحة") == "health"
    assert distribution_name_equivalent_key("Factures") == "bills"
    assert distribution_name_equivalent_key("لفواتير") == "bills"
    assert distribution_name_equivalent_key("Charges") == "housing_charges"
    assert distribution_name_equivalent_key("مصاريف السكن") == "housing_charges"
    assert distribution_name_equivalent_key("Famille — Aide") == "family_aid"
    assert distribution_name_equivalent_key("مساعدة العائلة") == "family_aid"

