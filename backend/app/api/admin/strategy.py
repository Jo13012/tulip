"""
admin/strategy.py — Global strategy management (single strategy model).

There is exactly ONE global strategy. Admin edits and publishes it.
No strategy creation or multi-strategy list — use /global endpoints.
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import AdminUser, DbSession
from app.db.models import Strategy, StrategySlot, StrategySlotVersion, StrategyVersion
from app.services.strategy_service import strategy_service
from app.integrations.polymarket.market_resolver import market_resolver
from app.runtime.session_manager import compute_viability

router = APIRouter(prefix="/admin/strategy", tags=["admin:strategy"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_global_strategy(db) -> Strategy:
    """Return the one global strategy (published or draft). 404 if none exists."""
    q = await db.execute(
        select(Strategy).order_by(Strategy.created_at.asc()).limit(1)
    )
    strat = q.scalars().first()
    if not strat:
        raise HTTPException(404, "Global strategy not found. Seed one via POST /admin/strategy/init")
    return strat


async def _strategy_detail(db, strat: Strategy) -> dict:
    slots_q = await db.execute(
        select(StrategySlot)
        .where(StrategySlot.strategy_id == strat.id, StrategySlot.archived_at.is_(None))
        .order_by(StrategySlot.order_index)
    )
    slots = slots_q.scalars().all()

    slot_data = []
    for slot in slots:
        sv_q = await db.execute(
            select(StrategySlotVersion)
            .where(StrategySlotVersion.strategy_slot_id == slot.id)
            .order_by(StrategySlotVersion.created_at.desc())
            .limit(1)
        )
        sv = sv_q.scalars().first()
        slot_data.append({
            "id": slot.id, "label": slot.label,
            "allocation_pct": slot.allocation_pct,
            "enabled": slot.enabled,
            "order_index": slot.order_index,
            "market": {
                "polymarket_url": sv.polymarket_url,
                "question": sv.question,
                "side": sv.side,
                "price_min": sv.price_min,
                "price_max": sv.price_max,
                "max_spread_ticks": sv.max_spread_ticks,
                "number_of_lots": sv.max_spread_ticks + 1,
                "sl_ticks": sv.sl_ticks,
                "spread_from_ask": sv.spread_from_ask,
                "token_id_yes": sv.token_id_yes,
                "token_id_no": sv.token_id_no,
            } if sv and sv.polymarket_url else None,
        })

    versions_q = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strat.id)
        .order_by(StrategyVersion.published_at.desc())
    )

    # Compute min_capital for draft (current slots)
    draft_viability_slots = [
        {"allocation_pct": s["allocation_pct"], "max_spread_ticks": s["market"]["max_spread_ticks"] if s["market"] else 1, "enabled": s["enabled"]}
        for s in slot_data
    ]
    draft_v = compute_viability(draft_viability_slots, strat.reserve_pct, 999_999)
    min_capital_draft = draft_v.min_capital

    # Compute min_capital for published (latest version)
    min_capital_published: float | None = None
    latest_ver_q = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strat.id)
        .order_by(StrategyVersion.published_at.desc())
        .limit(1)
    )
    latest_ver = latest_ver_q.scalars().first()
    if latest_ver:
        pub_ssv_q = await db.execute(
            select(StrategySlotVersion, StrategySlot)
            .join(StrategySlot, StrategySlotVersion.strategy_slot_id == StrategySlot.id)
            .where(StrategySlotVersion.strategy_version_id == latest_ver.id)
        )
        pub_rows = pub_ssv_q.all()
        pub_slots = [
            {
                "allocation_pct": ssv.allocation_pct if ssv.allocation_pct is not None else sl.allocation_pct,
                "max_spread_ticks": ssv.max_spread_ticks,
                "enabled": sl.enabled,
            }
            for ssv, sl in pub_rows
        ]
        if pub_slots:
            pub_v = compute_viability(pub_slots, strat.reserve_pct, 999_999)
            min_capital_published = pub_v.min_capital

    return {
        "id": strat.id, "name": strat.name,
        "status": strat.status, "reserve_pct": strat.reserve_pct,
        "fee_side": strat.fee_side,
        "global_pause": strat.global_pause,
        "version": f"{strat.current_major}.{strat.current_minor}",
        "slots": slot_data,
        "min_capital_draft": min_capital_draft,
        "min_capital_published": min_capital_published,
        "versions": [
            {"id": v.id, "label": v.version_label,
             "published_at": v.published_at.isoformat() if v.published_at else None}
            for v in versions_q.scalars().all()
        ],
    }


# ── Market resolution ─────────────────────────────────────────────────────────

@router.get("/resolve-event")
async def resolve_event(slug: str, admin: AdminUser):
    """Resolve a Polymarket event slug → list of sub-markets."""
    try:
        return await market_resolver.resolve_event(slug)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/book/{token_id}")
async def get_orderbook(token_id: str, admin: AdminUser):
    """Proxy live orderbook from Rust service."""
    try:
        return await market_resolver.get_book(token_id)
    except Exception as e:
        raise HTTPException(400, str(e))


# ── Schemas ───────────────────────────────────────────────────────────────────

class InitStrategyRequest(BaseModel):
    name: str = "Global Strategy"
    reserve_pct: float = 5.0
    fee_side: str = "none"


class UpdateStrategyRequest(BaseModel):
    name: Optional[str] = None
    reserve_pct: Optional[float] = None
    fee_side: Optional[str] = None
    global_pause: Optional[bool] = None


class PublishVersionRequest(BaseModel):
    kind: str = "minor"   # "minor" | "major"


class CreateSlotRequest(BaseModel):
    label: str
    allocation_pct: float
    order_index: int = 0


class SetSlotMarketRequest(BaseModel):
    polymarket_url: str
    side: str = "YES"
    price_min: float
    price_max: float
    number_of_lots: int = 2
    sl_ticks: int = 0
    spread_from_ask: bool = False
    resolve_now: bool = True


# ── Global strategy endpoints ─────────────────────────────────────────────────

@router.post("/init")
async def init_global_strategy(body: InitStrategyRequest, db: DbSession, admin: AdminUser):
    """
    Bootstrap the one global strategy. Call once on first deploy.
    Idempotent: returns existing strategy if one already exists.
    """
    existing_q = await db.execute(
        select(Strategy).limit(1)
    )
    existing = existing_q.scalars().first()
    if existing:
        return {"id": existing.id, "name": existing.name, "status": existing.status, "already_existed": True}

    strat = await strategy_service.create_strategy(
        db, body.name, body.reserve_pct, body.fee_side,
        actor_id=admin["sub"]
    )
    return {"id": strat.id, "name": strat.name, "status": strat.status, "already_existed": False}


@router.get("/global")
async def get_global_strategy(db: DbSession, admin: AdminUser):
    """Get the one global strategy with all slot details."""
    strat = await _get_global_strategy(db)
    return await _strategy_detail(db, strat)


@router.get("/global/slot-volume")
async def get_slot_volume_stats(db: DbSession, admin: AdminUser):
    """Per-slot volume/pnl/fees stats across all sessions, plus live engine data."""
    from app.db.models import SessionSlot as DBSessionSlot, SessionSlotMarketHistory, Session as DBSession
    from app.runtime.session_registry import session_registry
    import datetime

    strat = await _get_global_strategy(db)

    slots_q = await db.execute(
        select(StrategySlot)
        .where(StrategySlot.strategy_id == strat.id, StrategySlot.archived_at.is_(None))
        .order_by(StrategySlot.order_index)
    )
    strategy_slots = slots_q.scalars().all()

    live_engines = {e.session_id: e for e in await session_registry.all()}
    now = datetime.datetime.now(datetime.timezone.utc)

    results = []
    for ss in strategy_slots:
        # All session slots for this strategy slot
        ss_rows_q = await db.execute(
            select(DBSessionSlot).where(DBSessionSlot.strategy_slot_id == ss.id)
        )
        ss_rows = ss_rows_q.scalars().all()
        ss_ids = [r.id for r in ss_rows]
        session_ids = list({r.session_id for r in ss_rows if r.session_id})

        # DB history (closed)
        mh_q = await db.execute(
            select(SessionSlotMarketHistory)
            .where(SessionSlotMarketHistory.session_slot_id.in_(ss_ids))
        )
        markets = mh_q.scalars().all()
        db_vol  = sum((m.volume or 0) for m in markets)
        db_pnl  = sum((m.realized_pnl or 0) for m in markets)
        db_fees = sum((m.fees_paid or 0) for m in markets)

        # Live engine data — match by session_slot_id
        ss_id_set = set(ss_ids)
        live_vol = live_pnl = live_fees = 0.0
        for sid in session_ids:
            eng = live_engines.get(sid)
            if eng:
                for slot_rt in eng._slot_runtimes.values():
                    if slot_rt._session_slot_id in ss_id_set:
                        live_vol  += slot_rt._volume
                        live_pnl  += slot_rt._realized_pnl
                        live_fees += slot_rt._paid_fees

        total_vol  = round(db_vol + live_vol, 2)
        total_pnl  = round(db_pnl + live_pnl, 2)
        total_fees = round(db_fees + live_fees, 2)
        alloc = ss.allocation_pct or 0

        # Compute daily vol annualised from live sessions
        daily_vol = 0.0
        for sid in session_ids:
            eng = live_engines.get(sid)
            if eng:
                uptime_days = eng.uptime_s / 86400 if eng.uptime_s > 0 else None
                if uptime_days and uptime_days > 0:
                    for slot_rt in eng._slot_runtimes.values():
                        if slot_rt._session_slot_id in ss_id_set:
                            daily_vol += slot_rt._volume / uptime_days

        results.append({
            "slot_id":      ss.id,
            "label":        ss.label,
            "alloc_pct":    alloc,
            "total_volume": total_vol,
            "total_pnl":    total_pnl,
            "total_fees":   total_fees,
            "daily_volume": round(daily_vol, 2),
            "session_count": len(session_ids),
        })

    return results


@router.get("/global-published-slots")
async def get_published_slots(db: DbSession, admin: AdminUser):
    """Return slots as frozen in the latest published version."""
    strat = await _get_global_strategy(db)
    ver_q = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strat.id)
        .order_by(StrategyVersion.published_at.desc())
        .limit(1)
    )
    version = ver_q.scalars().first()
    if not version:
        return {"version": None, "slots": []}

    ssv_q = await db.execute(
        select(StrategySlotVersion, StrategySlot)
        .join(StrategySlot, StrategySlotVersion.strategy_slot_id == StrategySlot.id)
        .where(StrategySlotVersion.strategy_version_id == version.id)
        .order_by(StrategySlot.order_index)
    )
    rows = ssv_q.all()

    slots = []
    for ssv, slot in rows:
        slots.append({
            "id": slot.id, "label": slot.label,
            "allocation_pct": ssv.allocation_pct if ssv.allocation_pct is not None else slot.allocation_pct,
            "enabled": slot.enabled,
            "market": {
                "polymarket_url": ssv.polymarket_url,
                "question": ssv.question,
                "side": ssv.side,
                "price_min": ssv.price_min,
                "price_max": ssv.price_max,
                "number_of_lots": ssv.max_spread_ticks + 1,
                "sl_ticks": ssv.sl_ticks,
                "spread_from_ask": ssv.spread_from_ask,
            } if ssv.polymarket_url else None,
        })

    return {"version": version.version_label, "slots": slots}


@router.patch("/global")
async def update_global_strategy(body: UpdateStrategyRequest, db: DbSession, admin: AdminUser):
    strat = await _get_global_strategy(db)
    updated = await strategy_service.update_strategy(
        db, strat.id, actor_id=admin["sub"], **body.model_dump(exclude_none=True)
    )
    return {"ok": True, "id": updated.id}


@router.post("/global/publish")
async def publish_global_version(body: PublishVersionRequest, db: DbSession, admin: AdminUser):
    strat = await _get_global_strategy(db)
    version = await strategy_service.publish_strategy(
        db, strat.id, kind=body.kind, actor_id=admin["sub"]
    )
    await strategy_service.propagate_publish_to_active_sessions(
        db, strat.id, version, actor_id=admin["sub"]
    )
    return {"version_id": version.id, "label": version.version_label}


@router.post("/global/slots")
async def create_slot(body: CreateSlotRequest, db: DbSession, admin: AdminUser):
    strat = await _get_global_strategy(db)
    slot = await strategy_service.create_slot(
        db, strat.id, body.label, body.allocation_pct, body.order_index, actor_id=admin["sub"]
    )
    return {"id": slot.id, "label": slot.label}


class PatchSlotRequest(BaseModel):
    allocation_pct: Optional[float] = None
    enabled: Optional[bool] = None


@router.patch("/slots/{slot_id}")
async def patch_slot(slot_id: str, body: PatchSlotRequest, db: DbSession, admin: AdminUser):
    slot = await db.get(StrategySlot, slot_id)
    if not slot:
        raise HTTPException(404, "Slot not found")
    if body.allocation_pct is not None:
        slot.allocation_pct = body.allocation_pct
    if body.enabled is not None:
        slot.enabled = body.enabled
    db.add(slot)
    await db.commit()
    return {"ok": True}


@router.delete("/slots/{slot_id}")
async def delete_slot(slot_id: str, db: DbSession, admin: AdminUser):
    from datetime import datetime, timezone
    slot = await db.get(StrategySlot, slot_id)
    if not slot:
        raise HTTPException(404, "Slot not found")
    slot.archived_at = datetime.now(timezone.utc)
    db.add(slot)
    await db.commit()
    return {"ok": True}


@router.post("/slots/{slot_id}/restore")
async def restore_slot(slot_id: str, db: DbSession, admin: AdminUser):
    """Unarchive a deleted slot (restore to draft)."""
    slot = await db.get(StrategySlot, slot_id)
    if not slot:
        raise HTTPException(404, "Slot not found")
    slot.archived_at = None
    db.add(slot)
    await db.commit()
    return {"ok": True}


@router.post("/slots/{slot_id}/revert")
async def revert_slot(slot_id: str, db: DbSession, admin: AdminUser):
    """Revert slot draft market config to the latest published version."""
    strat_q = await db.execute(
        select(Strategy).order_by(Strategy.created_at.asc()).limit(1)
    )
    strat = strat_q.scalars().first()
    if not strat:
        raise HTTPException(404)

    ver_q = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strat.id)
        .order_by(StrategyVersion.published_at.desc())
        .limit(1)
    )
    version = ver_q.scalars().first()
    if not version:
        raise HTTPException(400, "No published version to revert to")

    pub_ssv_q = await db.execute(
        select(StrategySlotVersion).where(
            StrategySlotVersion.strategy_version_id == version.id,
            StrategySlotVersion.strategy_slot_id == slot_id,
        )
    )
    pub_ssv = pub_ssv_q.scalars().first()
    if not pub_ssv:
        raise HTTPException(400, "Slot not found in published version")

    # Also revert allocation_pct on the slot itself
    slot = await db.get(StrategySlot, slot_id)
    if not slot:
        raise HTTPException(404)

    # Create new draft SSV copying published values
    new_ssv = StrategySlotVersion(
        strategy_version_id=None,  # draft
        strategy_slot_id=slot_id,
        polymarket_url=pub_ssv.polymarket_url,
        question=pub_ssv.question,
        condition_id=pub_ssv.condition_id,
        token_id_yes=pub_ssv.token_id_yes,
        token_id_no=pub_ssv.token_id_no,
        side=pub_ssv.side,
        price_min=pub_ssv.price_min,
        price_max=pub_ssv.price_max,
        max_spread_ticks=pub_ssv.max_spread_ticks,
        sl_ticks=pub_ssv.sl_ticks,
        spread_from_ask=pub_ssv.spread_from_ask,
    )
    db.add(new_ssv)
    await db.commit()
    return {"ok": True}


# ── ID-based endpoints (legacy compat) ────────────────────────────────────────
# NOTE: these must stay AFTER all /global/* routes so FastAPI doesn't shadow them.

@router.get("/by-id/{strategy_id}")
async def get_strategy_by_id(strategy_id: str, db: DbSession, admin: AdminUser):
    strat = await db.get(Strategy, strategy_id)
    if not strat:
        raise HTTPException(404, "Not found")
    return await _strategy_detail(db, strat)


@router.patch("/by-id/{strategy_id}")
async def update_strategy_by_id(strategy_id: str, body: UpdateStrategyRequest, db: DbSession, admin: AdminUser):
    strat = await strategy_service.update_strategy(
        db, strategy_id, actor_id=admin["sub"], **body.model_dump(exclude_none=True)
    )
    if not strat:
        raise HTTPException(404)
    return {"ok": True, "id": strat.id}


@router.post("/by-id/{strategy_id}/publish")
async def publish_version_by_id(strategy_id: str, body: PublishVersionRequest, db: DbSession, admin: AdminUser):
    version = await strategy_service.publish_strategy(
        db, strategy_id, kind=body.kind, notes=body.notes, actor_id=admin["sub"]
    )
    await strategy_service.propagate_publish_to_active_sessions(
        db, strategy_id, version, actor_id=admin["sub"]
    )
    return {"version_id": version.id, "label": version.version_label}


@router.post("/by-id/{strategy_id}/slots")
async def create_slot_on_strategy(strategy_id: str, body: CreateSlotRequest, db: DbSession, admin: AdminUser):
    slot = await strategy_service.create_slot(
        db, strategy_id, body.label, body.allocation_pct, body.order_index, actor_id=admin["sub"]
    )
    return {"id": slot.id, "label": slot.label}


@router.post("/slots/{slot_id}/market")
async def set_slot_market(slot_id: str, body: SetSlotMarketRequest, db: DbSession, admin: AdminUser):
    question = condition_id = token_id_yes = token_id_no = None

    if body.resolve_now:
        try:
            info = await market_resolver.resolve_and_validate(body.polymarket_url)
            question     = info["question"]
            condition_id = info["condition_id"]
            token_id_yes = info["token_id_yes"]
            token_id_no  = info["token_id_no"]
        except Exception as e:
            raise HTTPException(400, f"Market resolve failed: {e}")

    ssv = await strategy_service.set_slot_market(
        db, slot_id, "draft", body.polymarket_url, question, condition_id,
        token_id_yes, token_id_no, body.side, body.price_min, body.price_max,
        body.number_of_lots, body.sl_ticks, body.spread_from_ask, actor_id=admin["sub"]
    )
    return {"slot_version_id": ssv.id, "question": ssv.question}


@router.post("/session-slots/{session_slot_id}/replace")
async def queue_slot_replacement(
    session_slot_id: str,
    body: SetSlotMarketRequest,
    db: DbSession,
    admin: AdminUser,
):
    question = condition_id = token_id_yes = token_id_no = None

    if body.resolve_now:
        try:
            info = await market_resolver.resolve_and_validate(body.polymarket_url)
            question=info["question"]; condition_id=info["condition_id"]
            token_id_yes=info["token_id_yes"]; token_id_no=info["token_id_no"]
        except Exception as e:
            raise HTTPException(400, f"Market resolve failed: {e}")

    from app.db.models import SessionSlot as DBSessionSlot
    ss = await db.get(DBSessionSlot, session_slot_id)
    if not ss:
        raise HTTPException(404, "Session slot not found")

    ssv = await strategy_service.set_slot_market(
        db, ss.strategy_slot_id, "draft", body.polymarket_url, question, condition_id,
        token_id_yes, token_id_no, body.side, body.price_min, body.price_max,
        body.number_of_lots, body.sl_ticks, body.spread_from_ask, actor_id=admin["sub"]
    )

    await strategy_service.queue_slot_replacement(db, session_slot_id, ssv.id, actor_id=admin["sub"])
    return {"ok": True, "slot_version_id": ssv.id}
