import asyncio
import logging
from uuid import UUID
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
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
)
from app.services.envelope_rules import normalize_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("merge_duplicates")


async def merge_user_envelopes(session: AsyncSession, user: User) -> None:
    # Load all envelopes for the user
    stmt = select(Envelope).where(Envelope.user_id == user.id)
    envelopes = list((await session.execute(stmt)).scalars().all())

    # Group by normalized name
    grouped = {}
    for env in envelopes:
        norm = normalize_name(env.name)
        grouped.setdefault(norm, []).append(env)

    for norm, group in grouped.items():
        if len(group) <= 1:
            if len(group) == 1 and group[0].name != norm:
                group[0].name = norm
                session.add(group[0])
            continue

        # Sort by created_at then ID to keep the oldest as the target
        group.sort(key=lambda e: (e.created_at or e.id, e.id))
        target = group[0]
        target.name = norm
        session.add(target)
        duplicates = group[1:]

        logger.info(
            f"Merging duplicates for user {user.email}: Target envelope '{target.name}' (ID: {target.id}), "
            f"Duplicates: {[(d.name, str(d.id)) for d in duplicates]}"
        )

        for duplicate in duplicates:
            # 1. Handle CategoryEnvelopeMap
            # Load maps for duplicate and target
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

            # 5. Handle EnvelopePeriod
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
                        # If a movement already exists for this transaction on the target period,
                        # combine their amounts instead of inserting a duplicate row.
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
                        # Sweep natural uniqueness check
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

    # No commit here so callers (like tests) can control transaction boundaries
    await session.flush()


async def main() -> None:
    async for db in get_db():
        # Load all users
        users_stmt = select(User)
        users = list((await db.execute(users_stmt)).scalars().all())

        for user in users:
            await merge_user_envelopes(db, user)
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
