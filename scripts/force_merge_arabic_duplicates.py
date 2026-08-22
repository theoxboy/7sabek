import asyncio
import logging
import re
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker
from app.models import (
    User,
    Envelope,
    EnvelopePeriod,
    EnvelopeMovement,
    EnvelopeAllocation,
    Sweep,
    CategoryEnvelopeMap,
    Goal,
    EnvelopeAdjustmentLog,
    EnvelopeTransferLog,
    DistributionItem,
    DistributionRule,
    DistributionRunItem,
    DistributionLogItem,
    DistributionSavedConfig,
)

from app.services.envelope_rules import normalize_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("force_merge_arabic_duplicates")


def advanced_normalize(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[ة]', 'ه', name)  # Convert Ta-Marbouta to Haa
    name = re.sub(r'[أإآ]', 'ا', name)  # Normalize Alif
    return name


async def get_envelope_balance(session: AsyncSession, envelope_id: UUID) -> Decimal:
    stmt = select(EnvelopePeriod).where(EnvelopePeriod.envelope_id == envelope_id)
    periods = list((await session.execute(stmt)).scalars().all())
    if not periods:
        return Decimal("0.00")
    
    # Sort periods by period_start DESC to find the latest period
    periods.sort(key=lambda p: p.period_start, reverse=True)
    latest_period = periods[0]
    
    period_id = latest_period.id
    opening = Decimal(str(latest_period.opening_balance))
    
    # Sum allocations
    allocs = (await session.execute(
        select(func.coalesce(func.sum(EnvelopeAllocation.amount), Decimal("0.00")))
        .where(EnvelopeAllocation.envelope_period_id == period_id)
    )).scalar_one()
    
    # Sum movements
    moves = (await session.execute(
        select(func.coalesce(func.sum(EnvelopeMovement.amount), Decimal("0.00")))
        .where(EnvelopeMovement.envelope_period_id == period_id)
    )).scalar_one()
    
    # Sum sweeps out
    sweeps_out = (await session.execute(
        select(func.coalesce(func.sum(Sweep.amount), Decimal("0.00")))
        .where(Sweep.from_envelope_period_id == period_id)
    )).scalar_one()
    
    # Sum sweeps in
    sweeps_in = (await session.execute(
        select(func.coalesce(func.sum(Sweep.amount), Decimal("0.00")))
        .where(Sweep.to_envelope_period_id == period_id)
    )).scalar_one()
    
    balance = opening + Decimal(str(allocs)) + Decimal(str(moves)) - Decimal(str(sweeps_out)) + Decimal(str(sweeps_in))
    return balance


async def merge_user_envelopes(session: AsyncSession, user: User) -> None:
    # Load all envelopes for the user
    stmt = select(Envelope).where(Envelope.user_id == user.id)
    envelopes = list((await session.execute(stmt)).scalars().all())

    # Group envelopes by advanced_normalize(envelope.name)
    grouped = {}
    for env in envelopes:
        norm = advanced_normalize(env.name)
        grouped.setdefault(norm, []).append(env)

    for norm, group in grouped.items():
        if len(group) <= 1:
            continue

        # Sort envelopes by balance DESC, then created_at ASC, then ID ASC
        envelope_balances = []
        for env in group:
            bal = await get_envelope_balance(session, env.id)
            envelope_balances.append((env, bal))

        from datetime import datetime
        min_dt = datetime.min
        envelope_balances.sort(key=lambda item: (-item[1], item[0].created_at or min_dt, item[0].id))

        target = envelope_balances[0][0]
        duplicates = [item[0] for item in envelope_balances[1:]]

        logger.info(
            f"Merging duplicates for user {user.email}: Target envelope '{target.name}' (ID: {target.id}), "
            f"Duplicates: {[(d.name, str(d.id)) for d in duplicates]}"
        )

        for duplicate in duplicates:
            # 1. Handle CategoryEnvelopeMap
            dup_maps_stmt = select(CategoryEnvelopeMap).where(
                CategoryEnvelopeMap.envelope_id == duplicate.id
            )
            dup_maps = list((await session.execute(dup_maps_stmt)).scalars().all())

            for map_item in dup_maps:
                # Check if target already has mapping for this category
                exist_stmt = select(CategoryEnvelopeMap).where(
                    CategoryEnvelopeMap.envelope_id == target.id,
                    CategoryEnvelopeMap.category_id == map_item.category_id,
                )
                exist_map = (await session.execute(exist_stmt)).scalar_one_or_none()
                if exist_map is not None:
                    # Target mapping already exists, safe to delete duplicate mapping
                    await session.delete(map_item)
                else:
                    # Reassign mapping to target envelope
                    map_item.envelope_id = target.id
                    session.add(map_item)
            await session.flush()

            # 2. Handle Goals
            goals_stmt = select(Goal).where(Goal.envelope_id == duplicate.id)
            goals = list((await session.execute(goals_stmt)).scalars().all())
            for goal in goals:
                goal.envelope_id = target.id
                session.add(goal)
            await session.flush()

            # 3. Handle EnvelopeAdjustmentLog
            adjust_stmt = select(EnvelopeAdjustmentLog).where(
                EnvelopeAdjustmentLog.envelope_id == duplicate.id
            )
            adjusts = list((await session.execute(adjust_stmt)).scalars().all())
            for adj in adjusts:
                adj.envelope_id = target.id
                session.add(adj)
            await session.flush()

            # 4. Handle EnvelopeTransferLog
            trans_from_stmt = select(EnvelopeTransferLog).where(
                EnvelopeTransferLog.from_envelope_id == duplicate.id
            )
            trans_from = list((await session.execute(trans_from_stmt)).scalars().all())
            for log in trans_from:
                log.from_envelope_id = target.id
                session.add(log)

            trans_to_stmt = select(EnvelopeTransferLog).where(
                EnvelopeTransferLog.to_envelope_id == duplicate.id
            )
            trans_to = list((await session.execute(trans_to_stmt)).scalars().all())
            for log in trans_to:
                log.to_envelope_id = target.id
                session.add(log)
            await session.flush()

            # 5. Handle DistributionItem (with uniqueness constraint handling)
            exist_item_stmt = select(DistributionItem).where(
                DistributionItem.user_id == duplicate.user_id,
                DistributionItem.target_type == "envelope",
                DistributionItem.target_id == target.id,
            )
            exist_item = (await session.execute(exist_item_stmt)).scalar_one_or_none()

            dup_items_stmt = select(DistributionItem).where(
                DistributionItem.target_type == "envelope",
                DistributionItem.target_id == duplicate.id,
            )
            dup_items = list((await session.execute(dup_items_stmt)).scalars().all())

            for item in dup_items:
                if exist_item is not None:
                    # Master already has a distribution item, delete duplicate item
                    await session.delete(item)
                else:
                    # Update duplicate item to point to master
                    item.target_id = target.id
                    session.add(item)
            await session.flush()

            # 6. Handle DistributionRule
            dup_rules_stmt = select(DistributionRule).where(
                DistributionRule.target_type == "envelope",
                DistributionRule.target_id == duplicate.id,
            )
            dup_rules = list((await session.execute(dup_rules_stmt)).scalars().all())
            for rule in dup_rules:
                rule.target_id = target.id
                session.add(rule)
            await session.flush()

            # 7. Handle DistributionRunItem
            dup_run_items_stmt = select(DistributionRunItem).where(
                DistributionRunItem.target_id == duplicate.id
            )
            dup_run_items = list((await session.execute(dup_run_items_stmt)).scalars().all())
            for run_item in dup_run_items:
                run_item.target_id = target.id
                session.add(run_item)
            await session.flush()

            # 8. Handle DistributionLogItem
            dup_log_items_stmt = select(DistributionLogItem).where(
                DistributionLogItem.target_id == duplicate.id
            )
            dup_log_items = list((await session.execute(dup_log_items_stmt)).scalars().all())
            for log_item in dup_log_items:
                log_item.target_id = target.id
                session.add(log_item)
            await session.flush()

            # 9. Handle DistributionSavedConfig JSONB rows
            configs_stmt = select(DistributionSavedConfig).where(
                DistributionSavedConfig.user_id == duplicate.user_id
            )
            configs = list((await session.execute(configs_stmt)).scalars().all())
            for config in configs:
                modified = False
                updated_rows = []
                for row in config.rows:
                    if row.get("target_type") == "envelope" and row.get("target_id") == str(duplicate.id):
                        row["target_id"] = str(target.id)
                        modified = True
                    updated_rows.append(row)
                if modified:
                    config.rows = updated_rows
                    session.add(config)
            await session.flush()

            # 10. Handle EnvelopePeriod
            dup_periods_stmt = select(EnvelopePeriod).where(
                EnvelopePeriod.envelope_id == duplicate.id
            )
            dup_periods = list((await session.execute(dup_periods_stmt)).scalars().all())

            for dup_period in dup_periods:
                # Check if target envelope has period with the same dates
                target_period_stmt = select(EnvelopePeriod).where(
                    EnvelopePeriod.envelope_id == target.id,
                    EnvelopePeriod.period_start == dup_period.period_start,
                    EnvelopePeriod.period_end == dup_period.period_end,
                )
                target_period = (
                    await session.execute(target_period_stmt)
                ).scalar_one_or_none()

                if target_period is None:
                    # No target period exists for these dates. Reassign duplicate period directly.
                    dup_period.envelope_id = target.id
                    session.add(dup_period)
                else:
                    # Target period exists. Migrate child objects from dup_period to target_period
                    # A. EnvelopeMovement
                    moves_stmt = select(EnvelopeMovement).where(
                        EnvelopeMovement.envelope_period_id == dup_period.id
                    )
                    moves = list((await session.execute(moves_stmt)).scalars().all())
                    for move in moves:
                        # Uniqueness check: uq_env_move_user_transaction
                        if move.transaction_id is not None:
                            exist_move_stmt = select(EnvelopeMovement).where(
                                EnvelopeMovement.envelope_period_id == target_period.id,
                                EnvelopeMovement.transaction_id == move.transaction_id,
                            )
                            exist_move = (
                                await session.execute(exist_move_stmt)
                            ).scalar_one_or_none()
                            if exist_move is not None:
                                exist_move.amount += move.amount
                                await session.delete(move)
                                session.add(exist_move)
                                continue

                        move.envelope_period_id = target_period.id
                        session.add(move)

                    # B. EnvelopeAllocation
                    allocs_stmt = select(EnvelopeAllocation).where(
                        EnvelopeAllocation.envelope_period_id == dup_period.id
                    )
                    allocs = list((await session.execute(allocs_stmt)).scalars().all())
                    for alloc in allocs:
                        alloc.envelope_period_id = target_period.id
                        session.add(alloc)

                    # C. Sweep
                    sweeps_from_stmt = select(Sweep).where(
                        Sweep.from_envelope_period_id == dup_period.id
                    )
                    sweeps_from = list(
                        (await session.execute(sweeps_from_stmt)).scalars().all()
                    )
                    for sweep in sweeps_from:
                        exist_sweep_stmt = select(Sweep).where(
                            Sweep.from_envelope_period_id == target_period.id,
                            Sweep.to_envelope_period_id == sweep.to_envelope_period_id,
                            Sweep.swept_on == sweep.swept_on,
                        )
                        exist_sweep = (
                            await session.execute(exist_sweep_stmt)
                        ).scalar_one_or_none()
                        if exist_sweep is not None:
                            exist_sweep.amount += sweep.amount
                            await session.delete(sweep)
                            session.add(exist_sweep)
                        else:
                            sweep.from_envelope_period_id = target_period.id
                            session.add(sweep)

                    sweeps_to_stmt = select(Sweep).where(
                        Sweep.to_envelope_period_id == dup_period.id
                    )
                    sweeps_to = list(
                        (await session.execute(sweeps_to_stmt)).scalars().all()
                    )
                    for sweep in sweeps_to:
                        exist_sweep_stmt = select(Sweep).where(
                            Sweep.from_envelope_period_id == sweep.from_envelope_period_id,
                            Sweep.to_envelope_period_id == target_period.id,
                            Sweep.swept_on == sweep.swept_on,
                        )
                        exist_sweep = (
                            await session.execute(exist_sweep_stmt)
                        ).scalar_one_or_none()
                        if exist_sweep is not None:
                            exist_sweep.amount += sweep.amount
                            await session.delete(sweep)
                            session.add(exist_sweep)
                        else:
                            sweep.to_envelope_period_id = target_period.id
                            session.add(sweep)

                    await session.flush()
                    # Re-link any rollover period pointer if it pointed to the duplicate
                    re_link_stmt = select(EnvelopePeriod).where(
                        EnvelopePeriod.rollover_from_period_id == dup_period.id
                    )
                    re_linked = list(
                        (await session.execute(re_link_stmt)).scalars().all()
                    )
                    for rp in re_linked:
                        rp.rollover_from_period_id = target_period.id
                        session.add(rp)

                    # Safe to delete duplicate period
                    await session.delete(dup_period)

            await session.flush()
            # Finally delete the duplicate envelope itself
            await session.delete(duplicate)

        # Flush deletes to PostgreSQL before updating name to avoid unique violations
        await session.flush()

        # After deleting duplicates, safely normalize the target's name
        target.name = normalize_name(target.name)
        session.add(target)
        await session.flush()

    await session.flush()


async def main() -> None:
    session_maker = get_sessionmaker()
    async with session_maker() as db:
        # Load all users
        users_stmt = select(User)
        users = list((await db.execute(users_stmt)).scalars().all())

        for user in users:
            await merge_user_envelopes(db, user)
        await db.commit()
        logger.info("Finished force merging Arabic duplicate envelopes successfully.")


if __name__ == "__main__":
    asyncio.run(main())
