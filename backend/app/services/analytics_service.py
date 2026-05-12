"""
analytics_service.py — Strategy / session / slot / market analytics.

All analytics queries aggregate from the DB.
No live runtime state — runtime state flows separately via WS.
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Session as DBSession, SessionSlot, SessionSlotMarketHistory,
    SessionLot, Strategy, StrategyVersion, StrategySlot, User, SessionSnapshot
)


class AnalyticsService:

    # ── Global admin metrics ──────────────────────────────────────────────────

    async def global_metrics(self, db: AsyncSession) -> dict:
        users_q     = await db.execute(select(func.count(User.id)))
        total_users = users_q.scalar() or 0

        sessions_q = await db.execute(select(DBSession))
        sessions   = sessions_q.scalars().all()

        active   = [s for s in sessions if s.status in ("running", "starting")]
        stopping = [s for s in sessions if s.status == "stopping"]
        recover  = [s for s in sessions if s.health_state == "recovering"]
        degraded = [s for s in sessions if s.health_state in ("degraded", "error")]

        total_gross      = sum(s.session_gross_capital      for s in active)
        total_deployable = sum(s.session_deployable_capital for s in active)

        # Pull live deployed capital + pnl/volume from running engines
        from app.runtime.session_registry import session_registry
        engines = await session_registry.all()
        live_by_session = {e.session_id: e for e in engines}

        total_deployed   = 0.0
        total_volume     = 0.0
        total_pnl        = 0.0
        total_fees       = 0.0
        for s in active:
            engine = live_by_session.get(s.id)
            if engine:
                sm = engine.summary
                total_deployed += sm.total_deployed
                total_volume   += sm.total_volume
                total_pnl      += sm.total_pnl
                total_fees     += (sm.total_volume or 0.0) * 40 / 10_000

        # Sum ALL users' trading balances from in-memory cache (balance_poller, zero I/O)
        from app.services.balance_poller import balance_poller
        total_trading = sum(
            (bal.trading_balance or 0.0)
            for bal in balance_poller._cache.values()
        )

        return {
            "total_users":              total_users,
            "active_sessions":          len(active),
            "stopping_sessions":        len(stopping),
            "recovering_sessions":      len(recover),
            "degraded_sessions":        len(degraded),
            "total_sessions":           len(sessions),
            "total_gross_capital":      round(total_gross, 2),
            "total_deployable_capital": round(total_deployable, 2),
            "total_deployed_capital":   round(total_deployed, 2),
            "total_trading_balance":    round(total_trading, 2),
            "live_total_volume":        round(total_volume, 2),
            "live_total_pnl":           round(total_pnl, 2),
            "live_total_fees":          round(total_fees, 2),
        }

    # ── Strategy analytics ────────────────────────────────────────────────────

    async def strategy_summary(self, db: AsyncSession, strategy_id: str) -> dict:
        from app.runtime.session_registry import session_registry
        live_engines = {e.session_id: e for e in await session_registry.all()}

        sessions_q = await db.execute(
            select(DBSession).where(DBSession.strategy_id == strategy_id)
        )
        sessions = sessions_q.scalars().all()
        live_session_ids    = {s.id for s in sessions if s.id in live_engines}
        stopped_session_ids = {s.id for s in sessions if s.id not in live_engines}

        slots_q = await db.execute(
            select(SessionSlot)
            .join(DBSession).where(DBSession.strategy_id == strategy_id)
        )
        slots = slots_q.scalars().all()
        total_allocated = sum((s.allocated_capital or 0) for s in slots)

        # Stopped sessions:
        #   volume/pnl → SUM(SessionSlot) — atomically incremented on every fill
        #   fees       → MAX(SessionSnapshot) per session (written every 5min + final stop)
        #                fallback to SessionSlot.paid_fees if no snapshot exists (crash < 5min)
        total_volume = total_pnl = total_fees = 0.0
        if stopped_session_ids:
            slot_q = await db.execute(
                select(
                    SessionSlot.session_id,
                    func.sum(SessionSlot.volume).label("vol"),
                    func.sum(SessionSlot.realized_pnl).label("pnl"),
                    func.sum(SessionSlot.paid_fees).label("slot_fees"),
                )
                .where(SessionSlot.session_id.in_(stopped_session_ids))
                .group_by(SessionSlot.session_id)
            )
            slot_rows = {r.session_id: r for r in slot_q.all()}

            snap_q = await db.execute(
                select(
                    SessionSnapshot.session_id,
                    func.max(SessionSnapshot.paid_fees).label("fees"),
                )
                .where(SessionSnapshot.session_id.in_(stopped_session_ids))
                .group_by(SessionSnapshot.session_id)
            )
            snap_fees = {r.session_id: float(r.fees or 0) for r in snap_q.all()}

            for sid in stopped_session_ids:
                row = slot_rows.get(sid)
                total_volume += float(row.vol or 0) if row else 0.0
                total_pnl    += float(row.pnl or 0) if row else 0.0
                # prefer snapshot fees (more accurate), fallback to slot fees
                if sid in snap_fees:
                    total_fees += snap_fees[sid]
                elif row:
                    total_fees += float(row.slot_fees or 0)

        # Live sessions: engine is authoritative
        for sid in live_session_ids:
            sm = live_engines[sid].summary
            live_vol = sm.total_volume or 0.0
            total_volume += live_vol
            total_pnl    += sm.realized_pnl or 0.0
            total_fees   += live_vol * 40 / 10_000

        return {
            "strategy_id":     strategy_id,
            "total_sessions":  len(sessions),
            "active_sessions": len(live_session_ids),
            "total_volume":    round(total_volume, 2),
            "total_pnl":       round(total_pnl, 2),
            "total_fees":      round(total_fees, 2),
            "volume_multiple": round(total_volume / total_allocated, 4) if total_allocated > 0 else 0.0,
        }

    # ── Slot performance analytics ────────────────────────────────────────────

    async def slot_performance(self, db: AsyncSession, strategy_slot_id: str) -> dict:
        slots_q = await db.execute(
            select(SessionSlot).where(SessionSlot.strategy_slot_id == strategy_slot_id)
        )
        slots = slots_q.scalars().all()

        mh_q = await db.execute(
            select(SessionSlotMarketHistory).where(
                SessionSlotMarketHistory.session_slot_id.in_([s.id for s in slots])
            )
        )
        markets = mh_q.scalars().all()

        total_allocated = sum((s.allocated_capital or 0) for s in slots)
        total_volume    = sum((m.volume or 0) for m in markets)
        total_pnl       = sum((m.realized_pnl or 0) for m in markets)
        total_fees      = sum((m.fees_paid or 0) for m in markets)

        return {
            "strategy_slot_id": strategy_slot_id,
            "session_count":    len(slots),
            "total_allocated":  round(total_allocated, 2),
            "total_volume":     round(total_volume, 2),
            "volume_multiple":  round(total_volume / total_allocated, 4) if total_allocated > 0 else 0.0,
            "total_pnl":        round(total_pnl, 2),
            "total_fees":       round(total_fees, 2),
            "market_count":     len(markets),
        }

    # ── Market-within-slot history analytics ──────────────────────────────────

    async def market_history_performance(
        self, db: AsyncSession, strategy_slot_id: Optional[str] = None
    ) -> list[dict]:
        q = select(SessionSlotMarketHistory)
        if strategy_slot_id:
            q = q.join(SessionSlot).where(SessionSlot.strategy_slot_id == strategy_slot_id)
        q = q.where(SessionSlotMarketHistory.ended_at.is_not(None))

        result = await db.execute(q)
        markets = result.scalars().all()

        agg: dict[str, dict] = {}
        for m in markets:
            key = m.polymarket_url
            if key not in agg:
                agg[key] = {
                    "polymarket_url": m.polymarket_url,
                    "question": m.question,
                    "volume": 0.0,
                    "realized_pnl": 0.0,
                    "fees_paid": 0.0,
                    "exposure_count": 0,
                }
            agg[key]["volume"]       += m.volume or 0
            agg[key]["realized_pnl"] += m.realized_pnl or 0
            agg[key]["fees_paid"]    += m.fees_paid or 0
            agg[key]["exposure_count"] += 1

        out = list(agg.values())
        for r in out:
            r["volume"]       = round(r["volume"], 2)
            r["realized_pnl"] = round(r["realized_pnl"], 2)
            r["fees_paid"]    = round(r["fees_paid"], 2)
        return sorted(out, key=lambda x: x["volume"], reverse=True)

    # ── Strategy version comparison ───────────────────────────────────────────

    async def version_performance(self, db: AsyncSession, strategy_id: str) -> list[dict]:
        from app.runtime.session_registry import session_registry

        versions_q = await db.execute(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.published_at)
        )
        versions = versions_q.scalars().all()

        # Live engines keyed by session_id
        live_engines = {e.session_id: e for e in await session_registry.all()}

        results = []
        for v in versions:
            sessions_q = await db.execute(
                select(DBSession).where(DBSession.start_strategy_version_id == v.id)
            )
            sessions = sessions_q.scalars().all()
            session_ids = [s.id for s in sessions]

            if not session_ids:
                results.append({
                    "version_id":    v.id,
                    "version_label": v.version_label,
                    "published_at":  v.published_at.isoformat() if v.published_at else None,
                    "is_current":    False,
                    "session_count": 0,
                    "total_volume":  0.0,
                    "total_pnl":     0.0,
                    "total_fees":    0.0,
                })
                continue

            live_sids    = {sid for sid in session_ids if sid in live_engines}
            stopped_sids = {sid for sid in session_ids if sid not in live_engines}

            total_volume = total_pnl = total_fees = 0.0
            if stopped_sids:
                slot_q = await db.execute(
                    select(
                        SessionSlot.session_id,
                        func.sum(SessionSlot.volume).label("vol"),
                        func.sum(SessionSlot.realized_pnl).label("pnl"),
                        func.sum(SessionSlot.paid_fees).label("slot_fees"),
                    )
                    .where(SessionSlot.session_id.in_(stopped_sids))
                    .group_by(SessionSlot.session_id)
                )
                slot_rows = {r.session_id: r for r in slot_q.all()}
                snap_q = await db.execute(
                    select(
                        SessionSnapshot.session_id,
                        func.max(SessionSnapshot.paid_fees).label("fees"),
                    )
                    .where(SessionSnapshot.session_id.in_(stopped_sids))
                    .group_by(SessionSnapshot.session_id)
                )
                snap_fees = {r.session_id: float(r.fees or 0) for r in snap_q.all()}
                for sid in stopped_sids:
                    row = slot_rows.get(sid)
                    total_volume += float(row.vol or 0) if row else 0.0
                    total_pnl    += float(row.pnl or 0) if row else 0.0
                    total_fees   += snap_fees[sid] if sid in snap_fees else (float(row.slot_fees or 0) if row else 0.0)

            # Live engines are authoritative for running sessions
            for sid in live_sids:
                sm = live_engines[sid].summary
                live_vol = sm.total_volume or 0.0
                total_volume += live_vol
                total_pnl    += sm.realized_pnl or 0.0
                total_fees   += live_vol * 40 / 10_000

            results.append({
                "version_id":    v.id,
                "version_label": v.version_label,
                "published_at":  v.published_at.isoformat() if v.published_at else None,
                "is_current":    bool(live_sids),
                "session_count": len(sessions),
                "total_volume":  round(total_volume, 2),
                "total_pnl":     round(total_pnl,    2),
                "total_fees":    round(total_fees,    2),
            })

        return results

    # ── Session-level estimate (for user UI) ──────────────────────────────────

    async def strategy_estimate_for_user(
        self, db: AsyncSession, strategy_id: str
    ) -> Optional[dict]:
        """
        Returns average performance data for user UI estimates.
        Returns None if insufficient data.
        MIN_DATA_SESSIONS = 3 closed sessions.
        """
        MIN_DATA_SESSIONS = 3

        sessions_q = await db.execute(
            select(DBSession).where(
                DBSession.strategy_id == strategy_id,
                DBSession.status == "stopped",
                DBSession.started_at.is_not(None),
                DBSession.stopped_at.is_not(None),
            )
        )
        sessions = sessions_q.scalars().all()

        if len(sessions) < MIN_DATA_SESSIONS:
            return None

        durations = []
        volumes   = []
        pnls      = []

        for s in sessions:
            dur = (s.stopped_at - s.started_at).total_seconds() / 86400  # days
            durations.append(dur)

            mh_q = await db.execute(
                select(SessionSlotMarketHistory)
                .join(SessionSlot)
                .where(SessionSlot.session_id == s.id)
            )
            markets = mh_q.scalars().all()
            vol = sum((m.volume or 0) for m in markets)
            pnl = sum((m.realized_pnl or 0) for m in markets)
            capital = s.session_deployable_capital or 1.0
            volumes.append(vol / capital)   # normalise by capital
            pnls.append(pnl / capital)

        n = len(sessions)
        avg_daily_volume_multiple = sum(volumes) / n / (sum(durations) / n) if sum(durations) > 0 else 0
        avg_pnl_pct = sum(pnls) / n * 100

        return {
            "avg_daily_volume_multiple": round(avg_daily_volume_multiple, 4),
            "avg_pnl_pct": round(avg_pnl_pct, 2),
            "sample_size": n,
            "disclaimer": "Past performance is not indicative of future results.",
        }


    # ── Daily volume chart data ───────────────────────────────────────────────

    async def daily_volume(self, db: AsyncSession, days: int = 60) -> list[dict]:
        """
        Daily volume + fees from snapshots (cumulative MAX per session per day → delta).
        Live engines: add incremental above last snapshot to avoid restart double-count.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        today_str = datetime.now(timezone.utc).date().isoformat()

        from app.runtime.session_registry import session_registry
        engines = await session_registry.all()
        live_session_ids = {e.session_id for e in engines}

        # Per session per day: max cumulative volume/fees snapshot
        sub = (
            select(
                cast(SessionSnapshot.snapshot_at, Date).label("day"),
                SessionSnapshot.session_id,
                func.max(SessionSnapshot.total_volume).label("vol"),
                func.max(SessionSnapshot.paid_fees).label("fees"),
            )
            .where(SessionSnapshot.snapshot_at >= since)
            .group_by(cast(SessionSnapshot.snapshot_at, Date), SessionSnapshot.session_id)
            .subquery()
        )
        result = await db.execute(
            select(sub.c.day, sub.c.session_id, sub.c.vol, sub.c.fees)
            .order_by(sub.c.session_id, sub.c.day)
        )
        snapshot_rows = result.all()

        # Compute daily incremental volume per session (delta from previous day's max)
        from collections import defaultdict
        session_days: dict[str, list[tuple]] = defaultdict(list)
        for row in snapshot_rows:
            session_days[row.session_id].append((
                str(row.day), float(row.vol or 0), float(row.fees or 0)
            ))

        vol_map: dict[str, float] = {}

        for session_id, day_rows in session_days.items():
            day_rows.sort(key=lambda x: x[0])
            prev_vol = 0.0
            for day_str, cum_vol, _cum_fees in day_rows:
                inc_vol = max(0.0, cum_vol - prev_vol)
                prev_vol = cum_vol
                vol_map[day_str] = vol_map.get(day_str, 0.0) + inc_vol

        # Live engines: add only what's above the last persisted snapshot (restart-safe)
        for engine in engines:
            sm = engine.summary
            snap_rows = session_days.get(engine.session_id, [])
            last_snap_vol = max((v for _, v, _ in snap_rows), default=0.0)
            today_vol = max(0.0, (sm.total_volume or 0.0) - last_snap_vol)
            vol_map[today_str] = vol_map.get(today_str, 0.0) + today_vol

        out = []
        for day_str in sorted(vol_map):
            vol = round(vol_map[day_str], 2)
            out.append({
                "date":   day_str,
                "volume": vol,
                "fees":   round(vol * 40 / 10_000, 2),
            })
        return out

    # ── Daily CPM chart data ──────────────────────────────────────────────────

    async def daily_cpm(self, db: AsyncSession, days: int = 60) -> list[dict]:
        """
        Returns per-day CPM = pnl / volume * 1_000_000.
        gross_cpm uses realized_pnl; net_cpm subtracts paid_fees.
        Uses MAX snapshot per session per day.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        today_str = datetime.now(timezone.utc).date().isoformat()

        from collections import defaultdict
        from app.runtime.session_registry import session_registry
        engines = await session_registry.all()

        sub = (
            select(
                cast(SessionSnapshot.snapshot_at, Date).label("day"),
                SessionSnapshot.session_id,
                func.max(SessionSnapshot.total_volume).label("vol"),
                func.max(SessionSnapshot.realized_pnl).label("pnl"),
                func.max(SessionSnapshot.paid_fees).label("fees"),
            )
            .where(SessionSnapshot.snapshot_at >= since)
            .group_by(cast(SessionSnapshot.snapshot_at, Date), SessionSnapshot.session_id)
            .order_by(SessionSnapshot.session_id, cast(SessionSnapshot.snapshot_at, Date))
            .subquery()
        )

        result_raw = await db.execute(
            select(sub.c.day, sub.c.session_id, sub.c.vol, sub.c.pnl, sub.c.fees)
            .order_by(sub.c.session_id, sub.c.day)
        )
        snapshot_rows_cpm = result_raw.all()

        # Compute daily incremental vol/pnl/fees per session (delta from previous day)
        session_days_cpm: dict[str, list[tuple]] = defaultdict(list)
        for row in snapshot_rows_cpm:
            session_days_cpm[row.session_id].append((
                str(row.day),
                float(row.vol or 0),
                float(row.pnl or 0),
                float(row.fees or 0),
            ))

        day_data: dict[str, dict] = {}
        for session_id, day_rows in session_days_cpm.items():
            day_rows.sort(key=lambda x: x[0])
            prev_vol, prev_pnl = 0.0, 0.0
            for day_str, cum_vol, cum_pnl, _cum_fees in day_rows:
                inc_vol = max(0.0, cum_vol - prev_vol)
                inc_pnl = cum_pnl - prev_pnl
                prev_vol, prev_pnl = cum_vol, cum_pnl
                if inc_vol < 0.5:
                    continue
                d = day_data.setdefault(day_str, {"vol": 0.0, "pnl": 0.0})
                d["vol"] += inc_vol
                d["pnl"] += inc_pnl

        for engine in engines:
            sm = engine.summary
            live_vol_total = sm.total_volume or 0.0
            live_pnl_total = sm.realized_pnl or 0.0
            if live_vol_total < 0.5:
                continue
            snap_rows = session_days_cpm.get(engine.session_id, [])
            last_vol = max((v for _, v, _, _ in snap_rows), default=0.0)
            last_pnl = max((p for _, _, p, _ in snap_rows), default=0.0) if snap_rows else 0.0
            today_vol = max(0.0, live_vol_total - last_vol)
            today_pnl = live_pnl_total - last_pnl
            if today_vol < 0.5:
                continue
            uptime_s = sm.uptime_s or 0.0
            from datetime import datetime as _dt
            now_utc = _dt.now(timezone.utc)
            secs_since_midnight = (now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second)
            elapsed_today = max(min(uptime_s, secs_since_midnight), 1.0)
            scale = 86400.0 / elapsed_today
            d = day_data.setdefault(today_str, {"vol": 0.0, "pnl": 0.0})
            d["vol"] += today_vol * scale
            d["pnl"] += today_pnl * scale

        out = []
        for day_str, d in sorted(day_data.items()):
            vol = d["vol"]
            fees = vol * 40 / 10_000
            gross_cpm = round(d["pnl"] / vol * 1_000_000, 1) if vol > 0 else None
            net_cpm   = round((d["pnl"] - fees) / vol * 1_000_000, 1) if vol > 0 else None
            out.append({
                "date":      day_str,
                "volume":    round(vol, 2),
                "gross_cpm": gross_cpm,
                "net_cpm":   net_cpm,
            })
        return out

    # ── Daily volume multiple chart data ─────────────────────────────────────

    async def daily_multiple(self, db: AsyncSession, days: int = 60) -> list[dict]:
        """
        Daily capital efficiency: volume traded that day / deployable capital.
        Uses snapshot deltas per day (same approach as daily_cpm).
        Active sessions contribute today's live totals.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        today_str = datetime.now(timezone.utc).date().isoformat()

        from app.runtime.session_registry import session_registry
        engines = await session_registry.all()

        # Per-session per-day: max cumulative volume and capital
        sub = (
            select(
                cast(SessionSnapshot.snapshot_at, Date).label("day"),
                SessionSnapshot.session_id,
                func.max(SessionSnapshot.total_volume).label("vol"),
                func.max(SessionSnapshot.deployable_capital).label("capital"),
            )
            .where(SessionSnapshot.snapshot_at >= since)
            .group_by(cast(SessionSnapshot.snapshot_at, Date), SessionSnapshot.session_id)
            .subquery()
        )

        result = await db.execute(
            select(sub.c.day, sub.c.session_id, sub.c.vol, sub.c.capital)
            .order_by(sub.c.session_id, sub.c.day)
        )
        snapshot_rows = result.all()

        # Compute daily incremental volume per session (delta from previous day)
        from collections import defaultdict
        session_days: dict[str, list[tuple]] = defaultdict(list)
        for row in snapshot_rows:
            session_days[row.session_id].append((str(row.day), float(row.vol or 0), float(row.capital or 1)))

        # day_data: date → {vol_increment, capital_sum, capital_count}
        day_data: dict[str, dict] = {}
        for session_id, day_rows in session_days.items():
            day_rows.sort(key=lambda x: x[0])
            prev_vol = 0.0
            for day_str, cum_vol, capital in day_rows:
                inc_vol = max(0.0, cum_vol - prev_vol)
                prev_vol = cum_vol
                if inc_vol < 0.5:
                    continue
                d = day_data.setdefault(day_str, {"vol": 0.0, "capital": 0.0, "n": 0})
                d["vol"] += inc_vol
                d["capital"] += capital
                d["n"] += 1

        # Add live sessions: only today's incremental volume, extrapolated to 24h
        from datetime import datetime as _dt
        now_utc = _dt.now(timezone.utc)
        secs_since_midnight = now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second
        for engine in engines:
            sm = engine.summary
            live_vol_total = sm.total_volume or 0.0
            if live_vol_total < 0.5:
                continue
            vol_in_snapshots = session_days.get(engine.session_id, [])
            last_snapshot_vol = max((v for _, v, _ in vol_in_snapshots), default=0.0)
            today_vol = max(0.0, live_vol_total - last_snapshot_vol)
            if today_vol < 0.5:
                continue
            uptime_s = sm.uptime_s or 0.0
            elapsed_today = max(min(uptime_s, secs_since_midnight), 1.0)
            scale = 86400.0 / elapsed_today
            sessions_q = await db.execute(
                select(DBSession.session_deployable_capital).where(DBSession.id == engine.session_id)
            )
            cap_row = sessions_q.first()
            capital = float(cap_row[0] or 1.0) if cap_row else 1.0
            d = day_data.setdefault(today_str, {"vol": 0.0, "capital": 0.0, "n": 0})
            d["vol"] += today_vol * scale
            d["capital"] += capital
            d["n"] += 1

        out = []
        for day_str, d in sorted(day_data.items()):
            vol = d["vol"]
            total_capital = d["capital"]  # sum of all session capitals active that day
            multiple = round(vol / total_capital, 4) if total_capital > 0 else 0.0
            out.append({
                "date":           day_str,
                "volume":         round(vol, 2),
                "run_days":       1.0,
                "capital":        round(total_capital, 2),
                "gross_multiple": multiple,
                "net_multiple":   multiple,
                "gross_pnl":      0.0,
                "fees":           0.0,
                "session_id":     f"day:{day_str}",
            })
        return out


analytics_service = AnalyticsService()
