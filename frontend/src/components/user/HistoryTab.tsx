import { useEffect, useState } from 'react'
import { api } from '../../utils/api'
import { fmtUsd, pnlColor } from '../../utils/format'
import { InfoTip } from '../ui/Tooltip'

interface HistorySession {
  session_id:     string
  status:         string
  gross_capital:  number
  stop_mode:      string
  stop_days:      number | null
  target_volume:  number | null
  started_at:     string | null
  stopped_at:     string | null
  stop_reason:    string | null
  total_volume:   number
  realized_pnl:   number
  unrealized_pnl: number
  paid_fees:      number
  trade_count:    number
  order_count:    number
}

function fmtShortDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function turnoverSub(s: HistorySession): { total: string; daily: string } | null {
  if (!s.gross_capital || s.gross_capital === 0) return null
  const totalX = s.total_volume / s.gross_capital
  const startMs = s.started_at ? new Date(s.started_at).getTime() : null
  const endMs   = s.stopped_at ? new Date(s.stopped_at).getTime() : Date.now()
  const uptimeDays = startMs ? (endMs - startMs) / 86400000 : null
  const dailyX = uptimeDays && uptimeDays > 0 ? totalX / uptimeDays : null
  return {
    total: `${totalX.toFixed(2)}×`,
    daily: dailyX != null ? `${dailyX.toFixed(2)}×` : '—',
  }
}

const HEADERS = ['Finished', 'Capital', 'Objective', 'Volume', 'Gross PnL', 'Fees', 'Net PnL', 'CPM', 'Orders']

export function HistoryTab() {
  const [sessions, setSessions] = useState<HistorySession[]>([])
  const [loading, setLoading]   = useState(true)
  const [hideZero, setHideZero] = useState(false)

  useEffect(() => {
    api.getSessionHistory()
      .then((d: any) => setSessions(
        (d as HistorySession[]).filter(s =>
          s.status === 'stopped' || s.status === 'error'
        )
      ))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const visible = hideZero ? sessions.filter(s => s.total_volume > 0) : sessions

  if (loading) {
    return (
      <div className="p-5 space-y-px">
        {[1, 2, 3].map(i => (
          <div key={i} className="h-10 bg-white/[0.025] animate-pulse rounded-l" />
        ))}
      </div>
    )
  }

  if (!sessions.length) {
    return (
      <div className="p-10 flex flex-col items-center justify-center gap-2">
        <div className="text-white/15 text-xs font-mono uppercase tracking-widest">No finished sessions</div>
        <div className="text-white/10 text-[10px]">Completed sessions will appear here</div>
      </div>
    )
  }

  return (
    <div>
      {/* ── Desktop table (sm+) ── */}
      <div className="hidden sm:block">
        <div className="relative border-b border-white/[0.05] bg-white/[0.015]">
          <div className="grid grid-cols-9 gap-2 px-5 py-2.5">
            {HEADERS.map(h => (
              <div key={h} className="flex items-center text-[9px] font-semibold uppercase tracking-widest text-white/20">
                {h}
                {h === 'CPM' && <InfoTip title="Cost per Million" content="Net PnL extrapolated to $1M of volume." />}
              </div>
            ))}
          </div>
          <button
            onClick={() => setHideZero(v => !v)}
            className={`absolute right-4 top-1/2 -translate-y-1/2 flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[9px] font-semibold uppercase tracking-widest transition-all ${
              hideZero
                ? 'border-brand/40 bg-brand/15 text-brand/80'
                : 'border-white/[0.08] bg-white/[0.03] text-white/25 hover:border-white/20 hover:text-white/45'
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full transition-colors ${hideZero ? 'bg-brand/80' : 'bg-white/20'}`} />
            Hide low
          </button>
        </div>

        {visible.map(s => {
          const netPnl = s.realized_pnl - s.paid_fees
          const cpm = s.total_volume > 0 ? (netPnl / s.total_volume) * 1_000_000 : null
          const cpmSign = cpm != null && cpm > 0 ? '+' : ''
          const isError = s.status === 'error'
          return (
            <div
              key={s.session_id}
              className="grid grid-cols-9 gap-2 px-5 py-3 border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] transition-colors"
            >
              <div className="flex flex-col justify-center gap-0.5">
                <div className="text-[10px] font-mono text-white/35">{fmtShortDate(s.stopped_at)}</div>
                {isError && <div className="text-[9px] font-mono text-red-400/60 truncate">{s.stop_reason || 'error'}</div>}
              </div>
              <div className="flex items-center">
                <span className="text-[11px] font-mono font-semibold text-white/55">{fmtUsd(s.gross_capital, 0)}</span>
              </div>
              <div className="flex items-center">
                <span className="text-[10px] font-mono text-white/25">
                  {s.stop_mode === 'days' ? `${s.stop_days}d` : s.target_volume ? `$${(s.target_volume / 1000).toFixed(0)}k` : 'vol'}
                </span>
              </div>
              <div className="flex flex-col justify-center gap-0.5">
                <span className="text-[11px] font-mono text-white/50">{fmtUsd(s.total_volume, 0)}</span>
                {(() => { const t = turnoverSub(s); return t ? <span className="text-[9px] font-mono text-white/20">{t.total} · {t.daily} / 24h</span> : null })()}
              </div>
              <div className="flex items-center">
                <span className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(s.realized_pnl)}`}>{fmtUsd(s.realized_pnl)}</span>
              </div>
              <div className="flex items-center">
                <span className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(s.paid_fees)}</span>
              </div>
              <div className="flex items-center">
                <span className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(netPnl)}`}>{fmtUsd(netPnl)}</span>
              </div>
              <div className="flex items-center">
                <span className={`text-[11px] font-mono font-semibold tabular-nums ${cpm != null ? pnlColor(cpm) : 'text-white/20'}`}>
                  {cpm != null ? `${cpmSign}${fmtUsd(cpm, 0)}` : '—'}
                </span>
              </div>
              <div className="flex items-center">
                <span className="text-[11px] font-mono text-white/40 tabular-nums">{s.order_count || s.trade_count}</span>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Mobile cards (xs only) ── */}
      <div className="sm:hidden">
        {/* Mobile header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.05] bg-white/[0.015]">
          <span className="text-[9px] font-semibold uppercase tracking-widest text-white/20">Sessions</span>
          <button
            onClick={() => setHideZero(v => !v)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[9px] font-semibold uppercase tracking-widest transition-all ${
              hideZero
                ? 'border-brand/40 bg-brand/15 text-brand/80'
                : 'border-white/[0.08] bg-white/[0.03] text-white/25'
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full transition-colors ${hideZero ? 'bg-brand/80' : 'bg-white/20'}`} />
            Hide low
          </button>
        </div>

        {visible.map(s => {
          const netPnl = s.realized_pnl - s.paid_fees
          const cpm = s.total_volume > 0 ? (netPnl / s.total_volume) * 1_000_000 : null
          const cpmSign = cpm != null && cpm > 0 ? '+' : ''
          const isError = s.status === 'error'
          return (
            <div key={s.session_id} className="px-4 py-3 border-b border-white/[0.04] last:border-0">
              <div className="flex items-center justify-between mb-2">
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] font-mono text-white/40">{fmtShortDate(s.stopped_at)}</span>
                  {isError && <span className="text-[9px] font-mono text-red-400/60">{s.stop_reason || 'error'}</span>}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[9px] font-mono text-white/25">
                    {s.stop_mode === 'days' ? `${s.stop_days}d` : s.target_volume ? `$${(s.target_volume / 1000).toFixed(0)}k vol` : 'vol'}
                  </span>
                  <span className="text-[11px] font-mono font-semibold text-white/55">{fmtUsd(s.gross_capital, 0)}</span>
                </div>
              </div>
              <div className="grid grid-cols-5 gap-x-2 gap-y-1">
                <div>
                  <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Volume</div>
                  <div className="text-[10px] font-mono text-white/50 tabular-nums">{fmtUsd(s.total_volume, 0)}</div>
                  {(() => { const t = turnoverSub(s); return t ? <><div className="text-[8px] font-mono text-white/20">{t.total} total</div><div className="text-[8px] font-mono text-white/20">{t.daily} 24h</div></> : null })()}
                </div>
                <div>
                  <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Gross</div>
                  <div className={`text-[10px] font-mono font-semibold tabular-nums ${pnlColor(s.realized_pnl)}`}>{fmtUsd(s.realized_pnl)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Fees</div>
                  <div className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(s.paid_fees)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Net</div>
                  <div className={`text-[10px] font-mono font-semibold tabular-nums ${pnlColor(netPnl)}`}>{fmtUsd(netPnl)}</div>
                </div>
                <div>
                  <div className="flex items-center text-[8px] text-white/20 uppercase tracking-widest mb-0.5">
                    CPM <InfoTip title="Cost per Million" content="Net PnL extrapolated to $1M of volume." />
                  </div>
                  <div className={`text-[10px] font-mono font-semibold tabular-nums ${cpm != null ? pnlColor(cpm) : 'text-white/20'}`}>
                    {cpm != null ? `${cpmSign}${fmtUsd(cpm, 0)}` : '—'}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
