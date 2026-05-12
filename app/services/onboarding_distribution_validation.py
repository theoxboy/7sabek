from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DistributionRule, DistributionSavedConfig, Envelope, User
from app.services.distribution_name_normalization import (
    distribution_name_equivalent_key,
)
from app.services.onboarding_v2_canonical import (
    CanonicalApplyState,
    compute_canonical_apply_state_backend,
)

logger = logging.getLogger(__name__)

def extract_distribution_eligible_names(
    *,
    answers: dict[str, Any],
) -> list[str]:
    canonical_state = compute_canonical_apply_state_backend(answers)
    return canonical_state.distribution_eligible_names


@dataclass
class ValidationResult:
    is_valid: bool = True
    blocking_errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    distribution_status: str = "not_required"
    distribution_source: str = "none"
    eligible_total: int = 0
    covered_total: int = 0
    unresolved_total: int = 0
    unresolved_envelope_names: list[str] = field(default_factory=list)
    missing_envelope_names: list[str] = field(default_factory=list)
    active_config_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_scope_name(value: str) -> str:
    return " ".join(value.split())


def _hash_scope_signature(value: str) -> str:
    # FNV-1a 32-bit, aligned with frontend hashScopeSignature.
    hash_value = 0x811C9DC5
    for char in value:
        hash_value ^= ord(char)
        hash_value = (hash_value * 0x01000193) & 0xFFFFFFFF
    return format(hash_value, "08x")


def _compute_distribution_scope_hash(eligible_names: list[str]) -> str:
    normalized = sorted(
        {
            distribution_name_equivalent_key(_normalize_scope_name(name))
            for name in eligible_names
            if isinstance(name, str) and _normalize_scope_name(name)
        },
        key=str.casefold,
    )
    canonical = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if len(canonical) <= 120:
        return canonical
    return f"h:{_hash_scope_signature(canonical)}:{len(normalized)}"


def build_apply_precondition_error_detail(result: ValidationResult) -> dict[str, Any]:
    first_message = (
        result.blocking_errors[0].get("message")
        if result.blocking_errors
        else "Onboarding apply preconditions failed."
    )
    return {
        "code": "ONBOARDING_APPLY_PRECONDITIONS_FAILED",
        "message": first_message,
        **result.to_dict(),
    }


async def validate_apply_preconditions(
    db: AsyncSession,
    *,
    current_user: User,
    canonical_state: CanonicalApplyState,
) -> ValidationResult:
    eligible_names = [
        name.strip()
        for name in canonical_state.distribution_eligible_names
        if isinstance(name, str) and name.strip()
    ]
    scope_hash = _compute_distribution_scope_hash(eligible_names)

    envelope_result = await db.execute(
        select(Envelope).where(
            Envelope.user_id == current_user.id,
            Envelope.is_cash.is_(False),
        )
    )
    envelopes = list(envelope_result.scalars().all())
    envelope_ids_by_key: dict[str, set[UUID]] = defaultdict(set)
    envelope_key_by_id: dict[UUID, str] = {}
    envelope_name_by_id = {env.id: env.name for env in envelopes}
    for env in envelopes:
        key = distribution_name_equivalent_key(env.name)
        if not key:
            continue
        envelope_ids_by_key[key].add(env.id)
        envelope_key_by_id[env.id] = key

    unresolved_names = [
        name
        for name in eligible_names
        if distribution_name_equivalent_key(name) not in envelope_ids_by_key
    ]
    eligible_keys = {
        distribution_name_equivalent_key(name)
        for name in eligible_names
        if distribution_name_equivalent_key(name) in envelope_ids_by_key
    }

    active_config_result = await db.execute(
        select(DistributionSavedConfig).where(
            DistributionSavedConfig.user_id == current_user.id,
            DistributionSavedConfig.is_active.is_(True),
        )
    )
    active_config = active_config_result.scalar_one_or_none()

    covered_ids: set[UUID] = set()
    source = "none"
    if active_config is not None and isinstance(active_config.rows, list):
        source = "active_config"
        for item in active_config.rows:
            if not isinstance(item, dict):
                continue
            if item.get("target_type") != "envelope":
                continue
            if item.get("mode") not in {"fixed", "percent"}:
                continue
            if bool(item.get("enabled", True)) is False:
                continue
            try:
                covered_ids.add(UUID(str(item.get("target_id"))))
            except Exception:
                continue
    else:
        rules_result = await db.execute(
            select(DistributionRule).where(DistributionRule.user_id == current_user.id)
        )
        rules = list(rules_result.scalars().all())
        for rule in rules:
            if (
                rule.target_type == "envelope"
                and rule.enabled
                and rule.mode in {"fixed_per_period", "percent_of_income"}
            ):
                covered_ids.add(rule.target_id)
        if covered_ids:
            source = "legacy_rules"

    covered_keys = {
        envelope_key_by_id[env_id]
        for env_id in covered_ids
        if env_id in envelope_key_by_id
    }
    missing_keys = [key for key in eligible_keys if key not in covered_keys]
    missing_key_set = set(missing_keys)
    missing_names: list[str] = []
    seen_missing: set[str] = set()
    for name in eligible_names:
        key = distribution_name_equivalent_key(name)
        if not key or key not in missing_key_set or key in seen_missing:
            continue
        missing_names.append(name)
        seen_missing.add(key)
    scope_mismatch = (
        active_config is not None
        and bool(scope_hash)
        and bool(active_config.scope_hash)
        and active_config.scope_hash != scope_hash
    )

    result = ValidationResult(
        distribution_source=source,
        eligible_total=len(eligible_keys),
        covered_total=len(eligible_keys) - len(missing_keys),
        unresolved_total=len(unresolved_names),
        unresolved_envelope_names=unresolved_names,
        missing_envelope_names=missing_names,
        active_config_id=str(active_config.id) if active_config else None,
    )

    if len(eligible_names) == 0 and len(unresolved_names) == 0:
        result.distribution_status = "not_required"
        if source == "legacy_rules":
            result.warnings.append(
                "Legacy distribution rules exist but the current onboarding state does not require flexible coverage."
            )
        return result

    if unresolved_names:
        result.warnings.append(
            "Certaines enveloppes de distribution requises ne sont pas synchronisées avec la configuration courante."
        )

    # Scope hash mismatch is informational only.
    # Effective coverage checks below remain the blocking source of truth.
    # This avoids false blockers when scope hash drifts across versions while
    # active distribution rows still correctly cover required envelopes.
    if scope_mismatch:
        result.warnings.append(
            "Distribution scope hash mismatch detected and ignored; effective coverage validation is used."
        )

    if result.eligible_total == 0:
        result.distribution_status = "invalid_targets"
        # Keep apply strict only when nothing can be resolved and there is no
        # active source to rely on.
        if unresolved_names and source == "none":
            result.blocking_errors.append(
                {
                    "code": "DISTRIBUTION_TARGETS_UNRESOLVED",
                    "message": "Certaines enveloppes de distribution requises ne sont pas synchronisées avec la configuration courante.",
                    "envelope_names": unresolved_names,
                }
            )
    elif result.covered_total == result.eligible_total:
        result.distribution_status = (
            "legacy_rules_complete" if source == "legacy_rules" else "saved_valid"
        )
        if source == "legacy_rules":
            result.warnings.append(
                "Legacy distribution rules are accepted for apply, but a saved distribution config is preferred."
            )
    elif source == "none":
        result.distribution_status = "setup_missing"
        result.blocking_errors.append(
            {
                "code": "DISTRIBUTION_REQUIRED_SETUP_MISSING",
                "message": "Aucune règle active ne couvre les enveloppes qui doivent être distribuées avant l'apply.",
                "envelope_names": eligible_names,
            }
        )
    elif source == "legacy_rules":
        result.distribution_status = "legacy_coverage_incomplete"
        result.blocking_errors.append(
            {
                "code": "LEGACY_DISTRIBUTION_COVERAGE_INSUFFICIENT",
                "message": "La source legacy ne couvre pas entièrement la configuration de distribution actuelle.",
                "envelope_names": missing_names or eligible_names,
            }
        )
    else:
        result.distribution_status = "coverage_incomplete"
        result.blocking_errors.append(
            {
                "code": "DISTRIBUTION_COVERAGE_INSUFFICIENT",
                "message": "La configuration de distribution active ne couvre pas toutes les enveloppes requises.",
                "envelope_names": missing_names or eligible_names,
            }
        )

    result.is_valid = len(result.blocking_errors) == 0
    if not result.is_valid:
        logger.warning(
            "onboarding apply precondition failed: source=%s status=%s eligible=%s covered=%s unresolved=%s missing=%s",
            result.distribution_source,
            result.distribution_status,
            result.eligible_total,
            result.covered_total,
            result.unresolved_total,
            result.missing_envelope_names,
        )
    return result
