from __future__ import annotations

from app.services.category_system_seed import build_system_category_mapping_plan


def test_build_system_category_mapping_plan_creates_transport_coverage() -> None:
    selected = [
        {"final_name": "Transport public", "group_key": "transport"},
    ]
    plan = build_system_category_mapping_plan(selected_envelopes=selected)
    assert plan["transport_public"] == "Transport public"
    assert plan["transport_fuel"] == "Transport public"
    assert plan["transport_generic"] == "Transport public"


def test_build_system_category_mapping_plan_creates_debt_coverage() -> None:
    selected = [
        {"final_name": "Dettes — credit", "group_key": "debts"},
    ]
    plan = build_system_category_mapping_plan(selected_envelopes=selected)
    assert plan["debt_payment"] == "Dettes — credit"
    assert plan["debt_extra_payment"] == "Dettes — credit"
    assert plan["taxes"] == "Dettes — credit"


def test_build_system_category_mapping_plan_infers_group_from_name() -> None:
    selected = [
        {"final_name": "الوقود", "group_key": None},
    ]
    plan = build_system_category_mapping_plan(selected_envelopes=selected)
    assert plan["transport_generic"] == "الوقود"


def test_build_system_category_mapping_plan_health_is_not_mapped_to_food() -> None:
    selected = [
        {"final_name": "الصحة", "group_key": None},
    ]
    plan = build_system_category_mapping_plan(selected_envelopes=selected)
    assert plan["health_generic"] == "الصحة"
    assert plan["health_consultation"] == "الصحة"
    assert plan["health_pharmacy"] == "الصحة"
    assert "groceries" not in plan
