"""Plugin-owned fire log — idempotency source + the tab's history fallback.

Isolated metadata via ``luna_sdk.declarative_base()`` + ``ctx.engine``; never
touches core's Base. The scheduler-service stays the source of truth for
triggers; this table only records fires this Luna actually received.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from luna_sdk import declarative_base

Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SchedulerFire(Base):
    __tablename__ = "plugin_scheduler_fires"

    fire_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trigger_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    action_type: Mapped[str] = mapped_column(String(32))
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    outcome: Mapped[str] = mapped_column(String(200), default="received")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SchedulerTriggerMeta(Base):
    """0.8.0 (089): provenance for triggers created through THIS Luna.

    The scheduler-service stays the source of truth for the trigger itself;
    this row only records who created it and from where, for fire routing
    (agent one-shots report into their origin chat, everything else into ops)
    and for the ephemeral lifecycle (an agent one-shot dies with its origin
    conversation). An ABSENT row means owner-created / durable — pre-0.8.0
    triggers need no backfill.
    """

    __tablename__ = "plugin_scheduler_trigger_meta"

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(16))  # 'owner' | 'agent'
    origin_conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    one_shot: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


async def create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        columns = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("plugin_scheduler_fires")}
        )
        if "payload_json" not in columns:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE plugin_scheduler_fires ADD COLUMN payload_json TEXT"))


async def seen(engine, fire_id: str) -> bool:
    async with engine.connect() as conn:
        row = await conn.execute(
            select(SchedulerFire.fire_id).where(SchedulerFire.fire_id == fire_id)
        )
        return row.first() is not None


async def record_fire(engine, *, fire_id: str, trigger_id: str | None,
                      trigger_name: str | None, action_type: str,
                      target: str | None, outcome: str,
                      payload_json: str | None = None) -> bool:
    try:
        async with engine.begin() as conn:
            await conn.execute(
                SchedulerFire.__table__.insert().values(
                    fire_id=fire_id, trigger_id=trigger_id, trigger_name=trigger_name,
                    action_type=action_type, target=target,
                    received_at=_utcnow(), outcome=outcome, payload_json=payload_json,
                )
            )
        return True
    except IntegrityError:
        return False


async def outcome_for(engine, fire_id: str) -> str | None:
    async with engine.connect() as conn:
        return (await conn.execute(
            select(SchedulerFire.outcome).where(SchedulerFire.fire_id == fire_id)
        )).scalar_one_or_none()


async def claim_dispatch(engine, fire_id: str) -> bool:
    async with engine.begin() as conn:
        result = await conn.execute(
            update(SchedulerFire)
            .where(SchedulerFire.fire_id == fire_id, SchedulerFire.outcome == "received")
            .values(outcome="dispatching")
        )
        return bool(result.rowcount)


async def pending_fires(engine) -> list[str]:
    async with engine.connect() as conn:
        rows = (await conn.execute(
            select(SchedulerFire.payload_json).where(
                SchedulerFire.outcome == "received",
                SchedulerFire.payload_json.is_not(None),
            )
        )).scalars().all()
        return list(rows)


async def set_outcome(engine, fire_id: str, outcome: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            SchedulerFire.__table__.update()
            .where(SchedulerFire.__table__.c.fire_id == fire_id)
            .values(outcome=outcome[:200])
        )


async def consecutive_failures(engine, trigger_id: str | None) -> int:
    """0.6.1: length of the current dead-fire streak for one trigger — newest
    fires first, counting error outcomes until the first success. In-flight
    and unknown-outcome rows do not break a prior error streak."""
    if not trigger_id:
        return 0
    async with engine.connect() as conn:
        rows = (await conn.execute(
            select(SchedulerFire.outcome)
            .where(SchedulerFire.trigger_id == trigger_id)
            .order_by(SchedulerFire.received_at.desc()).limit(20)
        )).all()
    streak = 0
    for (outcome,) in rows:
        if outcome in ("received", "dispatching") or outcome.startswith("unknown:"):
            continue
        if outcome.startswith("error"):
            streak += 1
        else:
            break
    return streak


def _meta_dict(r) -> dict:
    return {
        "trigger_id": r.trigger_id, "created_by": r.created_by,
        "origin_conversation_id": r.origin_conversation_id,
        "plan_id": r.plan_id, "one_shot": r.one_shot,
    }


async def record_trigger_meta(engine, *, trigger_id: str, created_by: str,
                              origin_conversation_id: str | None = None,
                              plan_id: str | None = None,
                              one_shot: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            SchedulerTriggerMeta.__table__.delete()
            .where(SchedulerTriggerMeta.__table__.c.trigger_id == trigger_id)
        )
        await conn.execute(
            SchedulerTriggerMeta.__table__.insert().values(
                trigger_id=trigger_id, created_by=created_by,
                origin_conversation_id=origin_conversation_id,
                plan_id=plan_id, one_shot=one_shot, created_at=_utcnow(),
            )
        )


async def get_trigger_meta(engine, trigger_id: str) -> dict | None:
    async with engine.connect() as conn:
        row = (await conn.execute(
            select(SchedulerTriggerMeta)
            .where(SchedulerTriggerMeta.trigger_id == trigger_id)
        )).first()
        return _meta_dict(row) if row else None


async def delete_trigger_meta(engine, trigger_id: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            SchedulerTriggerMeta.__table__.delete()
            .where(SchedulerTriggerMeta.__table__.c.trigger_id == trigger_id)
        )


async def agent_one_shots(engine, origin_conversation_id: str | None = None) -> list[dict]:
    """Agent-created one-shot triggers — the ephemeral ones that die with
    their origin conversation. Optionally scoped to one origin."""
    q = select(SchedulerTriggerMeta).where(
        SchedulerTriggerMeta.created_by == "agent",
        SchedulerTriggerMeta.one_shot.is_(True),
    )
    if origin_conversation_id is not None:
        q = q.where(
            SchedulerTriggerMeta.origin_conversation_id == origin_conversation_id
        )
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).all()
        return [_meta_dict(r) for r in rows]


async def recent_fires(engine, limit: int = 50) -> list[dict]:
    async with engine.connect() as conn:
        rows = (await conn.execute(
            select(SchedulerFire)
            .order_by(SchedulerFire.received_at.desc()).limit(limit)
        )).all()
        return [{
            "fire_id": r.fire_id, "trigger_id": r.trigger_id,
            "trigger_name": r.trigger_name, "action_type": r.action_type,
            "target": r.target, "received_at": r.received_at.isoformat(),
            "outcome": r.outcome,
        } for r in rows]
