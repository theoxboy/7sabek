from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import reset_engine
from app.models import DistributionRule, Envelope, Goal, OnboardingV2Record, User
from tests.onboarding_v2_apply_test_support import (
    build_answers,
    draft_objects_garbage,
    serialize_user_state,
)
from tests.utils import login_user, register_user


def _put_latest_record(
    client: TestClient,
    *,
    answers: dict[str, Any],
    draft_objects: dict[str, Any],
    stage: str = "review",
) -> None:
    response = client.put(
        "/users/me/onboarding-v2-records/latest",
        json={
            "flow_version": "v2",
            "stage": stage,
            "answers": answers,
            "draft_objects": draft_objects,
        },
    )
    assert response.status_code == 200, response.text


def _apply_latest_record(client: TestClient) -> dict[str, Any]:
    response = client.post("/users/me/onboarding-v2-records/latest/apply")
    assert response.status_code == 200, response.text
    return response.json()


def _apply_latest_record_error(client: TestClient, *, expected_status: int = 422) -> dict[str, Any]:
    response = client.post("/users/me/onboarding-v2-records/latest/apply")
    assert response.status_code == expected_status, response.text
    return response.json()


async def _build_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def _load_user_by_email(db: AsyncSession, email: str) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    return user


async def _load_latest_record(db: AsyncSession, user_id) -> OnboardingV2Record:
    result = await db.execute(
        select(OnboardingV2Record)
        .where(OnboardingV2Record.user_id == user_id)
        .order_by(OnboardingV2Record.created_at.desc())
        .limit(1)
    )
    return result.scalar_one()


def _build_answers_with_flexible_envelopes(*names: str) -> dict[str, Any]:
    answers = build_answers(include_explicit_envelope_answers=True)
    selected = list(answers.get("E11_selected_envelopes_v1", []))
    for name in names:
        selected.append(
            {
                "name": name,
                "final_name": name,
                "group_key": "essentials",
                "final_rollover_enabled": False,
                "custom_category": None,
                "custom_amount": None,
            }
        )
    answers["E11_selected_envelopes_v1"] = selected
    return answers


def _reset_app_engine() -> None:
    asyncio.run(reset_engine())


def _create_distribution_target_envelope(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post(
        "/envelopes",
        json={"name": name, "rollover_enabled": False},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_legacy_distribution_rule(
    client: TestClient,
    *,
    envelope_id: str,
    rank: int,
) -> dict[str, Any]:
    response = client.post(
        "/distribution/rules",
        json={
            "target_type": "envelope",
            "target_id": envelope_id,
            "mode": "fixed_per_period",
            "amount": "100.00",
            "percent": None,
            "priority": 50,
            "rank": rank,
            "enabled": True,
            "auto_apply_on_income": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_apply_endpoint_matches_legacy_and_modern_onboarding_records(
    app,
    database_url: str,
) -> None:
    legacy_answers = build_answers(include_explicit_envelope_answers=False)
    modern_answers = build_answers(
        include_explicit_envelope_answers=True,
        modernize=True,
    )
    suffix = uuid4().hex
    legacy_email = f"legacy-{suffix}@example.com"
    modern_email = f"modern-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, legacy_email, currency="MAD", sweep_interval_days=30)

        _put_latest_record(
            client,
            answers=legacy_answers,
            draft_objects={"envelopes_proposal_v1": {"selected_envelopes": [{"name": "Broken"}]}},
        )
        legacy_summary = _apply_latest_record(client)

        register_user(client, modern_email, currency="MAD", sweep_interval_days=30)

        _put_latest_record(
            client,
            answers=modern_answers,
            draft_objects=draft_objects_garbage(),
        )
        modern_summary = _apply_latest_record(client)

    assert legacy_summary["workflow_stage"] == "completed"
    assert legacy_summary["workflow_phase"] == "completed"
    assert legacy_summary["validation_stage"] == "valid"
    assert legacy_summary["materialization_stage"] == "applied"
    assert modern_summary["workflow_stage"] == "completed"
    assert modern_summary["workflow_phase"] == "completed"
    assert modern_summary["validation_stage"] == "valid"
    assert modern_summary["materialization_stage"] == "applied"

    async def _run() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            legacy_user = await _load_user_by_email(db, legacy_email)
            modern_user = await _load_user_by_email(db, modern_email)

            legacy_state = await serialize_user_state(db, legacy_user)
            modern_state = await serialize_user_state(db, modern_user)

            assert legacy_state == modern_state
            assert legacy_summary["selected_envelopes_count"] == modern_summary["selected_envelopes_count"] == 4
            assert legacy_summary["distribution_rules_created"] == modern_summary["distribution_rules_created"]
            assert legacy_summary["goal_distribution_rules_created"] == modern_summary["goal_distribution_rules_created"]

            rules_result = await db.execute(
                select(DistributionRule).order_by(DistributionRule.rank.asc())
            )
            rules = list(rules_result.scalars().all())
            assert len(rules) >= 4

            envelopes_result = await db.execute(select(Envelope).order_by(Envelope.name.asc()))
            envelopes = list(envelopes_result.scalars().all())
            assert any(envelope.name == "Loyer" for envelope in envelopes)

            goals_result = await db.execute(select(Goal).order_by(Goal.name.asc()))
            goals = list(goals_result.scalars().all())
            assert any(goal.name == "Voyage" for goal in goals)

    asyncio.run(_run())


def test_latest_record_storage_normalizes_legacy_answer_bridges_and_progress_snapshot(
    app,
    database_url: str,
) -> None:
    answers = build_answers(include_explicit_envelope_answers=True)
    answers["Q0b_primary_objective"] = ["debt"]
    answers["E7_lifestyle"] = "high"
    answers["E8_envelope_granularity"] = "detailed"
    draft_objects = {
        "onboarding_progress_v2": {
            "flow_stage": "questions",
            "step_index": 4,
            "current_question_id": "E11_envelope_setup",
            "is_rollover_config_screen": True,
        }
    }
    suffix = uuid4().hex
    email = f"normalize-write-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)
        _put_latest_record(client, answers=answers, draft_objects=draft_objects)

    async def _assert_db_state() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = await _load_latest_record(db, user.id)
            payload = record.payload
            assert isinstance(payload, dict)
            assert record.stage == "in_progress"
            stored_answers = payload.get("answers")
            assert isinstance(stored_answers, dict)
            assert stored_answers["F1_objectives_v1"] == ["debt"]
            assert stored_answers["F1_priority_profile_v1"] == {
                "debt_priority": "debt_relief_fast",
                "goal_priority": "goal_start_light",
                "living_priority": "living_balance",
            }
            assert stored_answers["E11_envelope_preferences_v1"] == {
                "lifestyle_margin_level": "high",
                "selected_suggestion_slugs": [
                    "loyer",
                    "factures",
                    "transport",
                    "dettes_visa",
                    "objectif_voyage",
                ]
            }
            for legacy_key in (
                "Q0b_primary_objective",
                "P1_priority_profile",
                "P1_debt_priority",
                "P1_goal_priority",
                "P1_living_priority",
                "E7_lifestyle",
                "E8_envelope_granularity",
                "E10_keep_suggestions",
            ):
                assert legacy_key not in stored_answers

            stored_draft_objects = payload.get("draft_objects")
            assert isinstance(stored_draft_objects, dict)
            progress = stored_draft_objects.get("onboarding_progress_v2")
            assert isinstance(progress, dict)
            assert progress == {
                "flow_stage": "questions",
                "step_index": 4,
                "current_question_id": "E12_smart_settings",
                "journey_mode": "money_plan",
                "step_id": "E12_smart_settings",
                "subview": "question",
                "modal_state": None,
                "review_context": None,
            }
            materialized_state = payload.get("materialized_state")
            assert isinstance(materialized_state, dict)
            summary = materialized_state.get("summary")
            assert isinstance(summary, dict)
            assert summary["workflow_stage"] == "in_progress"
            assert summary["workflow_phase"] == "planning"
            assert summary["validation_stage"] == "unknown"
            assert summary["materialization_stage"] == "not_applied"
            assert summary["state_is_consistent"] is True

    asyncio.run(_assert_db_state())


def test_latest_record_storage_distinguishes_planning_from_ready_for_apply(
    app,
    database_url: str,
) -> None:
    suffix = uuid4().hex
    onboarding_email = f"phase-onboarding-{suffix}@example.com"
    ready_email = f"phase-ready-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, onboarding_email, currency="MAD", sweep_interval_days=30)
        _put_latest_record(
            client,
            answers=build_answers(include_explicit_envelope_answers=True),
            draft_objects={
                "onboarding_progress_v2": {
                    "flow_stage": "questions",
                    "step_index": 8,
                    "current_question_id": "Q3a_housing_status",
                    "journey_mode": "onboarding",
                    "step_id": "Q3a_housing_status",
                    "subview": "journey_ready",
                    "modal_state": None,
                    "review_context": {"screen": "journey_ready"},
                }
            },
            stage="review",
        )
        onboarding_record_response = client.get("/users/me/onboarding-v2-records?limit=1")
        assert onboarding_record_response.status_code == 200, onboarding_record_response.text
        onboarding_latest = onboarding_record_response.json()[0]

        register_user(client, ready_email, currency="MAD", sweep_interval_days=30)
        _put_latest_record(
            client,
            answers={
                **build_answers(include_explicit_envelope_answers=True),
                "SWP2_last_income_amount": "6000",
            },
            draft_objects={
                "onboarding_progress_v2": {
                    "flow_stage": "questions",
                    "step_index": 4,
                    "current_question_id": "E12_smart_settings",
                    "journey_mode": "money_plan",
                    "step_id": "E12_smart_settings",
                    "subview": "question",
                    "modal_state": None,
                }
            },
            stage="in_progress",
        )
        ready_record_response = client.get("/users/me/onboarding-v2-records?limit=1")
        assert ready_record_response.status_code == 200, ready_record_response.text
        ready_latest = ready_record_response.json()[0]

    assert onboarding_latest["stage"] == "in_progress"
    onboarding_summary = onboarding_latest["payload"]["materialized_state"]["summary"]
    assert onboarding_summary["workflow_stage"] == "in_progress"
    assert onboarding_summary["workflow_phase"] == "planning"
    assert onboarding_summary["state_is_consistent"] is True

    assert ready_latest["stage"] == "review"
    ready_summary = ready_latest["payload"]["materialized_state"]["summary"]
    assert ready_summary["workflow_stage"] == "review"
    assert ready_summary["workflow_phase"] == "ready_for_apply"
    assert ready_summary["state_is_consistent"] is True


def test_latest_record_storage_derives_money_plan_progress_when_snapshot_is_missing(
    app,
    database_url: str,
) -> None:
    suffix = uuid4().hex
    email = f"missing-progress-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)
        _put_latest_record(
            client,
            answers=build_answers(include_explicit_envelope_answers=True),
            draft_objects={},
            stage="in_progress",
        )
        response = client.get("/users/me/onboarding-v2-records?limit=1")
        assert response.status_code == 200, response.text
        latest = response.json()[0]

    progress = latest["payload"]["draft_objects"]["onboarding_progress_v2"]
    assert progress == {
        "flow_stage": "questions",
        "step_index": 4,
        "current_question_id": "E12_smart_settings",
        "journey_mode": "money_plan",
        "step_id": "E12_smart_settings",
        "subview": "question",
        "modal_state": None,
        "review_context": None,
    }
    summary = latest["payload"]["materialized_state"]["summary"]
    assert latest["stage"] == "in_progress"
    assert summary["workflow_phase"] == "planning"
    assert summary["state_is_consistent"] is True


def test_primary_objective_sql_uses_canonical_modern_signals_on_save(
    app,
    database_url: str,
) -> None:
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)
    answers["F1_objectives_v1"] = ["savings"]
    suffix = uuid4().hex
    email = f"primary-save-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)
        response = client.put(
            "/users/me/onboarding-v2-records/latest",
            json={
                "flow_version": "v2",
                "stage": "in_progress",
                "answers": answers,
                "draft_objects": {},
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()

    assert payload["primary_objective"] == "debt"

    async def _assert_db_state() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = await _load_latest_record(db, user.id)
            assert record.primary_objective == "debt"

    asyncio.run(_assert_db_state())


def test_apply_endpoint_rejects_when_distribution_setup_is_missing(
    app,
    database_url: str,
) -> None:
    answers = _build_answers_with_flexible_envelopes("Courses")
    suffix = uuid4().hex
    email = f"missing-setup-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)
        _create_distribution_target_envelope(client, "Courses")
        _put_latest_record(client, answers=answers, draft_objects={})
        error = _apply_latest_record_error(client)

    assert error["detail"]["code"] == "ONBOARDING_APPLY_PRECONDITIONS_FAILED"
    assert error["detail"]["distribution_status"] == "setup_missing"
    assert error["detail"]["distribution_source"] == "none"
    assert error["detail"]["blocking_errors"]
    assert error["detail"]["blocking_errors"][0]["code"] == "DISTRIBUTION_REQUIRED_SETUP_MISSING"

    async def _assert_db_state() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = await _load_latest_record(db, user.id)
            assert record.stage == "in_progress"
            materialized_state = record.payload.get("materialized_state")
            assert isinstance(materialized_state, dict)
            summary = materialized_state.get("summary")
            assert isinstance(summary, dict)
            assert summary["workflow_stage"] == "in_progress"
            assert summary["workflow_phase"] == "planning"
            assert summary["validation_stage"] == "invalid"
            assert summary["materialization_stage"] == "not_applied"
            assert summary["blocking_errors"]

            goals_result = await db.execute(select(Goal).where(Goal.user_id == user.id))
            goals = list(goals_result.scalars().all())
            assert goals == []

            envelopes_result = await db.execute(
                select(Envelope).where(Envelope.user_id == user.id).order_by(Envelope.name.asc())
            )
            envelope_names = [item.name for item in envelopes_result.scalars().all()]
            assert envelope_names == ["Cash", "Courses", "Epargnes"]

    asyncio.run(_assert_db_state())


def test_apply_endpoint_accepts_complete_legacy_distribution_rules(
    app,
    database_url: str,
) -> None:
    answers = _build_answers_with_flexible_envelopes("Courses")
    suffix = uuid4().hex
    email = f"legacy-valid-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)
        course_envelope = _create_distribution_target_envelope(client, "Courses")
        _create_legacy_distribution_rule(
            client,
            envelope_id=course_envelope["id"],
            rank=1,
        )
        _put_latest_record(client, answers=answers, draft_objects={})
        summary = _apply_latest_record(client)

    assert summary["distribution_setup_valid"] is True
    assert summary["distribution_status"] == "legacy_rules_complete"
    assert summary["distribution_source"] == "legacy_rules"
    assert summary["distribution_validation_warnings"]
    assert summary["workflow_stage"] == "completed"
    assert summary["workflow_phase"] == "completed"
    assert summary["validation_stage"] == "valid"
    assert summary["materialization_stage"] == "applied"

    async def _assert_db_state() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = await _load_latest_record(db, user.id)
            assert record.stage == "completed"
            materialized_state = record.payload.get("materialized_state")
            assert isinstance(materialized_state, dict)
            record_summary = materialized_state.get("summary")
            assert isinstance(record_summary, dict)
            assert record_summary["workflow_stage"] == "completed"
            assert record_summary["workflow_phase"] == "completed"
            assert record_summary["validation_stage"] == "valid"
            assert record_summary["materialization_stage"] == "applied"

            goals_result = await db.execute(select(Goal).where(Goal.user_id == user.id))
            goals = list(goals_result.scalars().all())
            assert any(goal.name == "Voyage" for goal in goals)

    asyncio.run(_assert_db_state())


def test_apply_endpoint_rejects_partial_legacy_distribution_rules(
    app,
    database_url: str,
) -> None:
    answers = _build_answers_with_flexible_envelopes("Courses", "Santé")
    suffix = uuid4().hex
    email = f"legacy-partial-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)
        course_envelope = _create_distribution_target_envelope(client, "Courses")
        _create_distribution_target_envelope(client, "Santé")
        _create_legacy_distribution_rule(
            client,
            envelope_id=course_envelope["id"],
            rank=1,
        )
        _put_latest_record(client, answers=answers, draft_objects={})
        error = _apply_latest_record_error(client)

    assert error["detail"]["code"] == "ONBOARDING_APPLY_PRECONDITIONS_FAILED"
    assert error["detail"]["distribution_status"] == "legacy_coverage_incomplete"
    assert error["detail"]["distribution_source"] == "legacy_rules"
    assert error["detail"]["missing_envelope_names"] == ["Santé"]
    assert any(
        item["code"] == "LEGACY_DISTRIBUTION_COVERAGE_INSUFFICIENT"
        for item in error["detail"]["blocking_errors"]
    )

    async def _assert_db_state() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = await _load_latest_record(db, user.id)
            assert record.stage == "in_progress"
            materialized_state = record.payload.get("materialized_state")
            assert isinstance(materialized_state, dict)
            summary = materialized_state.get("summary")
            assert isinstance(summary, dict)
            assert summary["workflow_stage"] == "in_progress"
            assert summary["workflow_phase"] == "planning"
            assert summary["validation_stage"] == "invalid"
            assert summary["materialization_stage"] == "not_applied"
            assert any(
                item["code"] == "LEGACY_DISTRIBUTION_COVERAGE_INSUFFICIENT"
                for item in summary["blocking_errors"]
            )

            goals_result = await db.execute(select(Goal).where(Goal.user_id == user.id))
            goals = list(goals_result.scalars().all())
            assert goals == []

    asyncio.run(_assert_db_state())


def test_list_records_normalizes_legacy_answers_and_progress_for_read(
    app,
    database_url: str,
) -> None:
    suffix = uuid4().hex
    email = f"legacy-readable-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)

    async def _seed_legacy_record() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = OnboardingV2Record(
                user_id=user.id,
                flow_version="v2",
                stage="review",
                income_type="salaried",
                primary_objective=None,
                household_type="single",
                payload={
                    "answers": {
                        "Q0_income_type": "salaried",
                        "Q0b_primary_objective": ["debt", "all"],
                        "P1_debt_priority": "debt_relief_fast",
                        "P1_goal_priority": "goal_start_light",
                        "P1_living_priority": "living_balance",
                        "E7_lifestyle": "high",
                        "E10_keep_suggestions": ["loyer", "transport"],
                    },
                    "draft_objects": {
                        "onboarding_progress_v2": {
                            "flow_stage": "questions",
                            "step_index": 2,
                            "current_question_id": "E11_envelope_setup",
                            "is_ready_screen": True,
                        }
                    },
                    "materialized_state": {
                        "applied": False,
                        "applied_at": None,
                        "summary": {},
                    },
                },
            )
            db.add(record)
            await db.commit()

    asyncio.run(_seed_legacy_record())

    _reset_app_engine()
    with TestClient(app) as client:
        login_user(client, email)
        response = client.get("/users/me/onboarding-v2-records")
        assert response.status_code == 200, response.text
        records = response.json()

    assert len(records) >= 1
    latest = records[0]
    assert latest["primary_objective"] == "debt"
    answers = latest["payload"]["answers"]
    assert answers["F1_objectives_v1"] == ["debt", "all"]
    assert answers["F1_priority_profile_v1"] == {
        "debt_priority": "debt_relief_fast",
        "goal_priority": "goal_start_light",
        "living_priority": "living_balance",
    }
    assert answers["E11_envelope_preferences_v1"] == {
        "lifestyle_margin_level": "high",
        "selected_suggestion_slugs": ["loyer", "transport"],
    }
    assert "Q0b_primary_objective" not in answers
    assert "P1_debt_priority" not in answers
    assert latest["stage"] == "in_progress"
    progress = latest["payload"]["draft_objects"]["onboarding_progress_v2"]
    assert progress == {
        "flow_stage": "questions",
        "step_index": 2,
        "current_question_id": "E12_smart_settings",
        "journey_mode": "money_plan",
        "step_id": "E12_smart_settings",
        "subview": "question",
        "modal_state": None,
        "review_context": None,
    }
    summary = latest["payload"]["materialized_state"]["summary"]
    assert summary["stored_workflow_stage"] == "review"
    assert summary["workflow_stage"] == "in_progress"
    assert summary["workflow_phase"] == "planning"
    assert summary["state_is_consistent"] is False
    assert summary["state_inconsistency_code"] == "REVIEW_WITH_NON_READY_PHASE"


def test_apply_recalculates_stale_primary_objective_from_modern_guidance(
    app,
    database_url: str,
) -> None:
    answers = build_answers(include_explicit_envelope_answers=True, modernize=True)
    answers["F1_guidance_mode"] = "goal_growth_first"
    answers["F1_objectives_v1"] = ["debt"]
    suffix = uuid4().hex
    email = f"primary-apply-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)

    async def _seed_stale_record() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = OnboardingV2Record(
                user_id=user.id,
                flow_version="v2",
                stage="review",
                income_type="salaried",
                primary_objective="debt",
                household_type="single",
                payload={
                    "answers": answers,
                    "draft_objects": {},
                    "materialized_state": {
                        "applied": False,
                        "applied_at": None,
                        "summary": {},
                    },
                },
            )
            db.add(record)
            await db.commit()

    asyncio.run(_seed_stale_record())

    _reset_app_engine()
    with TestClient(app) as client:
        login_user(client, email)
        apply_response = client.post("/users/me/onboarding-v2-records/latest/apply")
        assert apply_response.status_code == 200, apply_response.text

        list_response = client.get("/users/me/onboarding-v2-records?limit=1")
        assert list_response.status_code == 200, list_response.text
        latest = list_response.json()[0]

    assert latest["primary_objective"] == "goals"

    async def _assert_db_state() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = await _load_latest_record(db, user.id)
            assert record.primary_objective == "goals"

    asyncio.run(_assert_db_state())


def test_list_records_marks_legacy_completed_invalid_record_as_inconsistent(app, database_url: str) -> None:
    suffix = uuid4().hex
    email = f"legacy-invalid-{suffix}@example.com"

    _reset_app_engine()
    with TestClient(app) as client:
        register_user(client, email, currency="MAD", sweep_interval_days=30)

    async def _seed_invalid_completed_record() -> None:
        sessionmaker = await _build_sessionmaker(database_url)
        async with sessionmaker() as db:
            user = await _load_user_by_email(db, email)
            record = OnboardingV2Record(
                user_id=user.id,
                flow_version="v2",
                stage="completed",
                income_type="salaried",
                primary_objective=None,
                household_type=None,
                payload={
                    "answers": {},
                    "draft_objects": {},
                    "materialized_state": {
                        "applied": True,
                        "applied_at": "2026-04-19T12:00:00+00:00",
                        "summary": {
                            "distribution_setup_valid": False,
                            "distribution_status": "coverage_incomplete",
                        },
                    },
                },
            )
            db.add(record)
            await db.commit()

    asyncio.run(_seed_invalid_completed_record())

    _reset_app_engine()
    with TestClient(app) as client:
        login_user(client, email)
        response = client.get("/users/me/onboarding-v2-records")
        assert response.status_code == 200, response.text
        records = response.json()

    assert len(records) >= 1
    latest = records[0]
    assert latest["stage"] == "review"
    summary = latest["payload"]["materialized_state"]["summary"]
    assert summary["stored_workflow_stage"] == "completed"
    assert summary["workflow_stage"] == "review"
    assert summary["workflow_phase"] == "ready_for_apply"
    assert summary["validation_stage"] == "invalid"
    assert summary["materialization_stage"] == "applied"
    assert summary["state_is_consistent"] is False
    assert summary["state_inconsistency_code"] == "COMPLETED_BUT_NOT_VALID"
