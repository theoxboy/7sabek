from __future__ import annotations

from app.services.onboarding_v2_canonical import compute_canonical_apply_state_backend


def test_canonical_fixed_expenses_include_new_items_other_rows_and_family_support() -> None:
    answers = {
        "Q0_income_type": "salaried",
        "S3_frequency": "monthly",
        "FX1_fixed_items": ["utilities", "childcare", "health", "subscriptions", "other"],
        "FX2_amount_utilities": "300",
        "FX2_amount_childcare": "500",
        "FX2_amount_health": "250",
        "FX2_amount_subscriptions": "120",
        "FX2_amount_other": "0",
        "FX3_other_fixed_rows": [
            {"name": "Netflix", "amount": 150, "cadence": "monthly"},
            {"name": "Syndic", "amount": 1200, "cadence": "quarterly"},
        ],
        "E6_support_family": "yes",
        "E6a_support_family_amount": "400",
        "E6b_support_family_cadence": "monthly",
    }

    state = compute_canonical_apply_state_backend(answers)
    by_label = {str(item.get("label")): item for item in state.cycle_normalized_expenses_v1}

    assert by_label["Eau/Électricité/Gaz"]["envelope"] == "Factures"
    assert by_label["Garde d'enfants"]["envelope"] == "École/Crèche"
    assert by_label["Santé/Pharmacie"]["envelope"] == "Santé"
    assert by_label["Abonnements"]["envelope"] == "Autres fixes"
    assert by_label["Netflix"]["envelope"] == "Netflix"
    assert by_label["Netflix"]["monthly_amount"] == 150.0
    assert by_label["Syndic"]["envelope"] == "Syndic"
    assert by_label["Syndic"]["monthly_amount"] == 400.0
    assert by_label["Aide famille"]["envelope"] == "Aide famille"
    assert by_label["Aide famille"]["monthly_amount"] == 400.0


def test_sanity_includes_other_rows_and_family_support_and_excludes_legacy_other_amount() -> None:
    answers = {
        "Q0_income_type": "salaried",
        "S2a_salary_amount": "5000",
        "S3_frequency": "monthly",
        "FX1_fixed_items": ["bills", "other"],
        "FX2_amount_bills": "300",
        "FX2_amount_other": "9999",  # must be ignored in modern flow
        "FX3_other_fixed_rows": [
            {"name": "Syndic", "amount": 1200, "cadence": "quarterly"},  # 400 monthly
        ],
        "E6_support_family": "yes",
        "E6a_support_family_amount": "100",
        "E6b_support_family_cadence": "weekly",  # 433.33 monthly equivalent
    }

    state = compute_canonical_apply_state_backend(answers)
    sanity = state.sanity_metrics

    # fixed = 300 + 400 + 433.33 = 1133.33
    assert sanity["fixedTotal"] == 1133.33
    assert sanity["remaining"] == 3866.67


def test_distribution_eligible_excludes_commitment_envelopes_even_when_group_key_is_wrong() -> None:
    answers = {
        "Q0_income_type": "salaried",
        "S2a_salary_amount": "7000",
        "S3_frequency": "monthly",
        "RNT0_rent_amount": "2500",
        "FX1_fixed_items": ["bills"],
        "FX2_amount_bills": "600",
        "E11_selected_envelopes_v1": [
            {
                "name": "Loyer",
                "final_name": "Loyer",
                "group_key": "essentials",  # wrong classification from payload
                "final_rollover_enabled": True,
            },
            {
                "name": "Factures",
                "final_name": "Factures",
                "group_key": "lifestyle",  # wrong classification from payload
                "final_rollover_enabled": True,
            },
            {
                "name": "الطوارئ",
                "final_name": "الطوارئ",
                "group_key": "buffer",
                "final_rollover_enabled": True,
            },
        ],
    }

    state = compute_canonical_apply_state_backend(answers)
    eligible = set(state.distribution_eligible_names)
    assert "Loyer" not in eligible
    assert "Factures" not in eligible
    assert "الطوارئ" in eligible


def test_distribution_eligible_excludes_fixed_with_multilingual_name_mismatch() -> None:
    answers = {
        "Q0_income_type": "salaried",
        "S2a_salary_amount": "7000",
        "S3_frequency": "monthly",
        "RNT0_rent_amount": "2500",
        "FX1_fixed_items": ["bills"],
        "FX2_amount_bills": "600",
        "E11_selected_envelopes_v1": [
            {
                "name": "Factures",
                "final_name": "لفواتير",
                "group_key": "bills",
                "final_rollover_enabled": True,
            },
            {
                "name": "Loyer",
                "final_name": "الكراء",
                "group_key": "housing",
                "final_rollover_enabled": True,
            },
            {
                "name": "Imprévus / طوارئ",
                "final_name": "الطوارئ",
                "group_key": "buffer",
                "final_rollover_enabled": True,
            },
        ],
    }

    state = compute_canonical_apply_state_backend(answers)
    eligible = set(state.distribution_eligible_names)
    assert "لفواتير" not in eligible
    assert "الكراء" not in eligible
    assert "الطوارئ" in eligible


def test_distribution_eligible_excludes_guidance_locked_targets() -> None:
    answers = {
        "Q0_income_type": "salaried",
        "S2a_salary_amount": "7000",
        "S3_frequency": "monthly",
        "E11_selected_envelopes_v1": [
            {
                "name": "Objectif — master",
                "final_name": "Objectif — master",
                "group_key": "goals",
                "lock_reason": "guidance_locked",
                "final_rollover_enabled": True,
            },
            {
                "name": "Imprévus / طوارئ",
                "final_name": "الطوارئ",
                "group_key": "buffer",
                "final_rollover_enabled": True,
            },
        ],
    }

    state = compute_canonical_apply_state_backend(answers)
    eligible = set(state.distribution_eligible_names)
    assert "Objectif — master" not in eligible
    assert "الطوارئ" in eligible
