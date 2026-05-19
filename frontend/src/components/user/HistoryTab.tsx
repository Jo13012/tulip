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
  paid_fees:           number
  builder_fees_usdc:   number
  builder_code_enabled: boolean
  trade_count:    number
  order_count:    number
}

function fmtDurShort(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  if (d > 0) return `${d}d ${h}h`
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function fmtProgressCell(s: HistorySession): { line1: string; pct: string } {
  if (s.stop_mode === 'days' && s.stop_days) {
    const startMs  = s.started_at  ? new Date(s.started_at).getTime()  : null
    const endMs    = s.stopped_at  ? new Date(s.stopped_at).getTime()  : Date.now()
    const elapsedS = startMs ? (endMs - startMs) / 1000 : 0
    const totalS   = s.stop_days * 86400
    const pct      = totalS > 0 ? Math.min(100, (elapsedS / totalS) * 100) : 0
    return {
      line1: `${fmtDurShort(elapsedS)} / ${s.stop_days}d`,
      pct:   `(${pct.toFixed(0)}%)`,
    }
  }
  if (s.stop_mode === 'volume' && s.target_volume) {
    const pct = Math.min(100, (s.total_volume / s.target_volume) * 100)
    const fmtK = (v: number) => v >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v.toFixed(0)}`
    return {
      line1: `${fmtK(s.total_volume)} / ${fmtK(s.target_volume)}`,
      pct:   `(${pct.toFixed(0)}%)`,
    }
  }
  return { line1: 'vol', pct: '' }
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

const HEADERS = ['Finished', 'Capital', 'Progress', 'Volume', 'Gross PnL', 'Fees', 'Net PnL', 'CPM']

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
      {/* Toggle above both views */}
      <div className="flex justify-end px-4 sm:px-5 py-2 border-b border-white/[0.04]">
        <button
          onClick={() => setHideZero(v => !v)}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[9px] font-semibold uppercase tracking-widest transition-all ${
            hideZero
              ? 'border-brand/40 bg-brand/15 text-brand/80'
              : 'border-white/[0.08] bg-white/[0.03] text-white/25 hover:border-white/20 hover:text-white/45'
          }`}
        >
          <span className={`w-1.5 h-1.5 rounded-full transition-colors ${hideZero ? 'bg-brand/80' : 'bg-white/20'}`} />
          Hide low
        </button>
      </div>

      {/* ── Desktop table (sm+) ── */}
      <div className="hidden sm:block">
        {/* Header */}
        <div className="px-5 py-2.5 border-b border-white/[0.05] bg-white/[0.015]">
          <div className="grid grid-cols-8 gap-2">
            {HEADERS.map(h => (
              <div key={h} className="flex items-center text-[9px] font-semibold uppercase tracking-widest text-white/20 whitespace-nowrap">
                {h}
                {h === 'CPM' && <InfoTip title="Cost per Million" content="Net PnL extrapolated to $1M of volume." />}
              </div>
            ))}
          </div>
        </div>

        {visible.map(s => {
          const newFeeModel = s.stopped_at != null && s.stopped_at >= '2026-05-13'
          const fees = newFeeModel ? s.builder_fees_usdc : s.paid_fees
          const netPnl = s.realized_pnl - fees
          const cpm = s.total_volume > 0 ? (netPnl / s.total_volume) * 1_000_000 : null
          const cpmSign = cpm != null && cpm > 0 ? '+' : ''
          const isError = s.status === 'error'
          return (
            <div
              key={s.session_id}
              className="grid grid-cols-8 gap-2 px-5 py-3 border-b border-white/[0.04] last:border-0 hover:bg-white/[0.015] transition-colors"
            >
              <div className="flex flex-col justify-center gap-0.5 min-w-0">
                <div className="text-[10px] font-mono text-white/35 truncate">{fmtShortDate(s.stopped_at)}</div>
                {isError && <div className="text-[9px] font-mono text-red-400/60 truncate">{s.stop_reason || 'error'}</div>}
              </div>
              <div className="flex items-center">
                <span className="text-[11px] font-mono font-semibold text-white/55 tabular-nums">{fmtUsd(s.gross_capital, 0)}</span>
              </div>
              <div className="flex flex-col justify-center gap-0.5">
                {(() => { const p = fmtProgressCell(s); return <>
                  <span className="text-[10px] font-mono text-white/30 tabular-nums">{p.line1}</span>
                  {p.pct && <span className="text-[9px] font-mono text-white/18">{p.pct}</span>}
                </> })()}
              </div>
              <div className="flex flex-col justify-center gap-0.5">
                <span className="text-[11px] font-mono text-white/50 tabular-nums">{fmtUsd(s.total_volume, 0)}</span>
                {(() => { const t = turnoverSub(s); return t ? <span className="text-[9px] font-mono text-white/20">{t.total} · {t.daily}/24h</span> : null })()}
              </div>
              <div className="flex items-center">
                <span className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(s.realized_pnl)}`}>{fmtUsd(s.realized_pnl)}</span>
              </div>
              <div className="flex items-center">
                <span className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(fees)}</span>
              </div>
              <div className="flex items-center">
                <span className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(netPnl)}`}>{fmtUsd(netPnl)}</span>
              </div>
              <div className="flex items-center">
                <span className={`text-[11px] font-mono font-semibold tabular-nums ${cpm != null ? pnlColor(cpm) : 'text-white/20'}`}>
                  {cpm != null ? `${cpmSign}${fmtUsd(cpm, 0)}` : '—'}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Mobile cards (xs only) ── */}
      <div className="sm:hidden">
        {visible.map(s => {
          const newFeeModel = s.stopped_at != null && s.stopped_at >= '2026-05-13'
          const fees = newFeeModel ? s.builder_fees_usdc : s.paid_fees
          const netPnl = s.realized_pnl - fees
          const cpm = s.total_volume > 0 ? (netPnl / s.total_volume) * 1_000_000 : null
          const cpmSign = cpm != null && cpm > 0 ? '+' : ''
          const isError = s.status === 'error'
          const to = turnoverSub(s)
          return (
            <div key={s.session_id} className="px-4 py-3.5 border-b border-white/[0.04] last:border-0">
              {/* Row 1: date + capital */}
              <div className="flex items-start justify-between mb-2.5">
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] font-mono text-white/40">{fmtShortDate(s.stopped_at)}</span>
                  {isError && <span className="text-[9px] font-mono text-red-400/60">{s.stop_reason || 'error'}</span>}
                  {(() => { const p = fmtProgressCell(s); return (
                    <span className="text-[9px] font-mono text-white/22">{p.line1} {p.pct}</span>
                  ) })()}
                </div>
                <span className="text-[13px] font-mono font-semibold text-white/60 tabular-nums">{fmtUsd(s.gross_capital, 0)}</span>
              </div>
              {/* Row 2: Volume full width */}
              <div className="mb-2.5 pb-2.5 border-b border-white/[0.04]">
                <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Volume</div>
                <div className="text-[11px] font-mono text-white/55 tabular-nums font-semibold">{fmtUsd(s.total_volume, 0)}</div>
                {to && <div className="text-[9px] font-mono text-white/22 mt-0.5">{to.total} total · {to.daily} / 24h</div>}
              </div>
              {/* Row 3: 4-col PnL stats */}
              <div className="grid grid-cols-4 gap-x-3 gap-y-0">
                <div>
                  <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Gross</div>
                  <div className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(s.realized_pnl)}`}>{fmtUsd(s.realized_pnl)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Fees</div>
                  <div className="text-[11px] font-mono text-white/25 tabular-nums">{fmtUsd(fees)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-white/20 uppercase tracking-widest mb-0.5">Net</div>
                  <div className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(netPnl)}`}>{fmtUsd(netPnl)}</div>
                </div>
                <div>
                  <div className="flex items-center text-[8px] text-white/20 uppercase tracking-widest mb-0.5">
                    CPM <InfoTip title="Cost per Million" content="Net PnL extrapolated to $1M of volume." />
                  </div>
                  <div className={`text-[11px] font-mono font-semibold tabular-nums ${cpm != null ? pnlColor(cpm) : 'text-white/20'}`}>
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
