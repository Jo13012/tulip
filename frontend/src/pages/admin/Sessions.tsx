import { useEffect, useState, useRef } from 'react'
import { api } from '../../utils/api'
import { useAppStore } from '../../store'
import { fmtUsd, fmtDate, fmtDuration, pnlColor, healthColor } from '../../utils/format'

// ─────────────────────────────────────────────────────────────────────────────
// Small UI atoms
// ─────────────────────────────────────────────────────────────────────────────

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-[#0a0b0d] px-3.5 py-2.5">
      <div className="text-[9px] text-white/20 uppercase tracking-widest font-semibold mb-1">{label}</div>
      <div className={`text-[13px] font-bold font-mono tabular-nums leading-none ${color ?? 'text-white/70'}`}>{value}</div>
    </div>
  )
}

const dotCls: Record<string, string> = {
  running:   'bg-emerald-400',
  starting:  'bg-yellow-400 animate-pulse',
  recovering:'bg-pink-400 animate-pulse',
  degraded:  'bg-red-400',
  error:     'bg-red-500',
  stopping:  'bg-yellow-400 animate-pulse',
  stopped:   'bg-white/15',
  idle:      'bg-white/15',
}

function SlotStateChip({ state }: { state: string }) {
  const cls: Record<string, string> = {
    active:                   'text-emerald-400',
    replacement_queued:       'text-amber-400',
    waiting_previous_clean:   'text-amber-400',
    switching:                'text-blue-400',
    idle:                     'text-white/25',
    disabled:                 'text-white/15',
    error:                    'text-red-400',
  }
  return <span className={`text-[10px] font-mono font-semibold ${cls[state] ?? 'text-white/30'}`}>{state}</span>
}

// ─────────────────────────────────────────────────────────────────────────────
// Session detail panel
// ─────────────────────────────────────────────────────────────────────────────

function AdminStopModal({ sessionId, isStopping, onClose, onDone }: {
  sessionId: string; isStopping: boolean; onClose: () => void; onDone: () => void
}) {
  const addToast = useAppStore(s => s.addToast)
  const [stopping, setStopping] = useState(false)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  async function stop(mode: 'soft' | 'hard') {
    setStopping(true)
    try {
      await api.admin.stopSession(sessionId, mode)
      addToast({ type: mode === 'soft' ? 'info' : 'warning', message: mode === 'soft' ? 'Soft stop initiated' : 'Hard stop executed' })
      onDone()
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
      setStopping(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm px-4"
      onClick={e => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className="bg-[#0f1012] border border-white/[0.08] rounded-2xl w-full max-w-sm shadow-2xl overflow-hidden">
        <div className="flex items-center justify-between px-5 pt-5 pb-4 border-b border-white/[0.05]">
          <span className="text-sm font-semibold text-white/80">Stop Session</span>
          <button className="w-6 h-6 flex items-center justify-center rounded-lg text-white/20 hover:text-white/55 hover:bg-white/[0.06] transition-all" onClick={onClose}>✕</button>
        </div>
        <div className="p-5 space-y-3">
          {!isStopping && (
            <button
              className="w-full text-left p-4 border border-white/[0.07] rounded-xl hover:border-white/14 hover:bg-white/[0.025] transition-all group disabled:opacity-40"
              disabled={stopping}
              onClick={() => stop('soft')}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[13px] font-semibold text-white/70 group-hover:text-white/85">Soft Stop</span>
                <span className="text-[9px] font-mono text-white/20 uppercase tracking-widest">Recommended</span>
              </div>
              <p className="text-[11px] text-white/30 leading-relaxed">Cancels buy orders. Open positions exit at target price or stop-loss.</p>
            </button>
          )}
          <button
            className="w-full text-left p-4 border border-red-500/12 rounded-xl hover:border-red-500/25 hover:bg-red-500/[0.04] transition-all group disabled:opacity-40"
            disabled={stopping}
            onClick={() => stop('hard')}
          >
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[13px] font-semibold text-red-400/60 group-hover:text-red-400/85">Hard Stop</span>
              <span className="text-[9px] font-mono text-red-400/30 uppercase tracking-widest">Immediate</span>
            </div>
            <p className="text-[11px] text-white/25 leading-relaxed">Cancels all orders and market-sells all positions immediately.</p>
          </button>
        </div>
        {stopping && <div className="px-5 pb-5 text-center text-[11px] font-mono text-white/20">Stopping…</div>}
      </div>
    </div>
  )
}

function SessionDetail({ id }: { id: string }) {
  const addToast = useAppStore((s) => s.addToast)
  const [s, setS]               = useState<any>(null)
  const [stopModal, setStopModal] = useState(false)
  const [replModal, setReplModal] = useState<string | null>(null)
  const [replForm, setReplForm]   = useState({
    polymarket_url: '', side: 'YES', price_min: 0.9, price_max: 0.99,
    number_of_lots: 1, sl_ticks: 0, spread_from_ask: false,
  })

  useEffect(() => {
    setS(null)
    let stopped = false
    const poll = () => {
      if (stopped) return
      api.admin.getSession(id).then((d: any) => {
        if (!stopped) setS(d)
        if (['stopped', 'error'].includes(d?.status)) stopped = true
      }).catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 2000)
    return () => { clearInterval(iv); stopped = true }
  }, [id])

  if (!s) {
    return (
      <div className="p-5 space-y-3">
        {[1,2,3].map(i => <div key={i} className="h-12 bg-white/[0.02] rounded-xl animate-pulse" />)}
      </div>
    )
  }

  const reload = () => api.admin.getSession(id).then(setS).catch(() => {})


  async function retryRecovery() {
    try { await api.admin.retryRecovery(id); addToast({ type: 'info', message: 'Recovery queued' }); reload() }
    catch (e: any) { addToast({ type: 'error', message: e.message }) }
  }

  async function queueReplacement(ssId: string) {
    try {
      await api.admin.queueReplacement(ssId, { ...replForm, resolve_now: true })
      addToast({ type: 'success', message: 'Replacement queued' })
      setReplModal(null); reload()
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
  }

  const rt        = s.runtime
  const canStop    = !['stopped', 'error'].includes(s.status)
  const isStopping = s.status === 'stopping' || s.health_state === 'stopping'
  const grossPnl  = rt ? rt.realized_pnl + rt.unrealized_pnl : 0

  return (
    <div className="space-y-5">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className={`w-2 h-2 rounded-full shrink-0 ${dotCls[s.health_state] ?? 'bg-white/20'}`} />
            <span className={`text-sm font-bold uppercase tracking-widest ${healthColor(s.health_state)}`}>
              {s.health_state}
            </span>
            {rt && (
              <span className="text-[10px] font-mono text-white/20">· {fmtDuration(rt.uptime_s)}</span>
            )}
          </div>
          <div className="text-[10px] font-mono text-white/20">
            {s.session_id} · {s.user_id?.slice(0, 12)}…
          </div>
          <div className="text-[10px] text-white/20 mt-0.5">Started {fmtDate(s.started_at)}</div>
          {s.stop_reason && (
            <div className="text-[10px] text-red-400/70 mt-0.5">{s.stop_reason}</div>
          )}
        </div>
        <div className="flex gap-2 shrink-0">
          {(s.health_state === 'recovering' || s.health_state === 'degraded') && (
            <button
              className="px-3 py-1.5 border border-amber-400/20 rounded-lg text-[11px] text-amber-400/70 hover:border-amber-400/40 hover:text-amber-400 transition-all"
              onClick={retryRecovery}
            >
              Retry Recovery
            </button>
          )}
          {canStop && (
            <button
              className="px-3 py-1.5 border border-red-500/20 rounded-lg text-[11px] text-red-400/60 hover:border-red-500/40 hover:text-red-400 transition-all"
              onClick={() => setStopModal(true)}
            >
              Stop
            </button>
          )}
        </div>
      </div>

      {/* Stats grid */}
      {rt && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-white/[0.04] border border-white/[0.04] rounded-xl overflow-hidden">
          <Stat label="Gross"       value={fmtUsd(rt.gross_capital)} />
          <Stat label="Deployed"    value={fmtUsd(rt.total_deployed)} />
          <Stat label="Volume"      value={fmtUsd(rt.total_volume)} />
          <Stat label="Trades"      value={String(rt.trade_count)} />
          <Stat label="Gross PnL"   value={fmtUsd(grossPnl)}       color={pnlColor(grossPnl)} />
          <Stat label="Net PnL"     value={fmtUsd(grossPnl - rt.paid_fees)} color={pnlColor(grossPnl - rt.paid_fees)} />
          <Stat label="Fees Paid"   value={fmtUsd(rt.paid_fees)}   color="text-white/30" />
          <Stat label="Slots"       value={`${rt.active_slot_count}/${rt.slot_count}`} />
        </div>
      )}

      {/* Slots */}
      <div className="border border-white/[0.05] rounded-xl overflow-hidden">
        <div className="px-4 py-2.5 border-b border-white/[0.05] bg-white/[0.015]">
          <span className="text-[9px] font-semibold uppercase tracking-widest text-white/20">Slots</span>
        </div>
        {s.slots?.map((ss: any) => (
          <div key={ss.session_slot_id} className="border-b border-white/[0.04] last:border-0 px-4 py-3 hover:bg-white/[0.015] transition-colors">
            <div className="flex items-center justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <SlotStateChip state={ss.slot_state} />
                  <span className="text-[10px] text-white/20 font-mono">{ss.session_slot_id.slice(0, 8)}</span>
                </div>
                <div className="text-[10px] text-white/35">
                  {ss.allocation_pct}% · {fmtUsd(ss.allocated_capital)} allocated
                </div>
                {ss.runtime && (
                  <div className="text-[10px] text-white/20 mt-0.5 font-mono">
                    deployed {fmtUsd(ss.runtime.deployed_capital)}
                    {' · '}lots {ss.runtime.open_lots}
                    {' · '}<span className="text-white/35">{fmtUsd(ss.runtime.volume, 0)} vol</span>
                    {' · '}pnl{' '}
                    <span className={pnlColor(ss.runtime.realized_pnl)}>{fmtUsd(ss.runtime.realized_pnl)}</span>
                  </div>
                )}
              </div>
              {canStop && (
                <button
                  className="px-2.5 py-1 border border-white/[0.08] rounded-lg text-[10px] text-white/30 hover:border-white/20 hover:text-white/55 transition-all shrink-0"
                  onClick={() => setReplModal(ss.session_slot_id)}
                >
                  Replace
                </button>
              )}
            </div>

            {ss.market_history?.length > 0 && (
              <div className="mt-2 space-y-0.5 pl-2 border-l border-white/[0.04]">
                {ss.market_history.slice(0, 3).map((m: any, i: number) => (
                  <div key={i} className="text-[10px] text-white/20 flex gap-3 font-mono">
                    <span className="truncate max-w-[180px]">{m.question || m.url}</span>
                    <span className="text-white/15">{fmtDate(m.started_at)}</span>
                    <span>{fmtUsd(m.volume, 0)} vol</span>
                    <span className={pnlColor(m.pnl)}>{fmtUsd(m.pnl)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Logs */}
      {s.logs?.length > 0 && (
        <div className="border border-white/[0.05] rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 border-b border-white/[0.05] bg-white/[0.015]">
            <span className="text-[9px] font-semibold uppercase tracking-widest text-white/20">Recent Logs</span>
          </div>
          <div className="max-h-96 overflow-y-auto font-mono text-[10px] p-3 space-y-0.5">
            {s.logs.slice(-200).reverse().map((l: any, i: number) => (
              <div key={i} className={`flex gap-2 ${
                l.level === 'ERROR'   ? 'text-red-400' :
                l.level === 'WARN'    ? 'text-amber-400' :
                l.level === 'SUCCESS' ? 'text-emerald-400' : 'text-white/30'
              }`}>
                <span className="text-white/15 flex-shrink-0 w-16">{l.timestamp?.slice(11, 19)}</span>
                {l.repeat > 1 && (
                  <span className="flex-shrink-0 text-white/20 bg-white/[0.06] px-1 rounded">×{l.repeat}</span>
                )}
                <span className="break-all">{l.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stop modal */}
      {stopModal && (
        <AdminStopModal
          sessionId={id}
          isStopping={isStopping}
          onClose={() => setStopModal(false)}
          onDone={() => { setStopModal(false); reload() }}
        />
      )}

      {/* Replace market modal */}
      {replModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-[#0f1012] border border-white/[0.08] rounded-xl p-5 w-96 space-y-3 shadow-2xl">
            <div className="text-sm font-semibold text-white/80">Replace Market</div>
            <div className="text-[10px] text-white/25 font-mono">Slot: {replModal.slice(0, 8)}</div>

            <input
              className="w-full bg-transparent border border-white/[0.08] rounded-xl px-3.5 py-2.5 text-white font-mono text-sm placeholder:text-white/15 focus:outline-none focus:border-brand/50 transition-all"
              placeholder="Polymarket URL"
              value={replForm.polymarket_url}
              onChange={e => setReplForm(f => ({ ...f, polymarket_url: e.target.value }))}
            />

            <div className="grid grid-cols-2 gap-2">
              {([
                ['Side', 'side', 'select', ['YES', 'NO']],
                ['Lots', 'number_of_lots', 'number', null, 1],
                ['Price Min', 'price_min', 'decimal', null],
                ['Price Max', 'price_max', 'decimal', null],
              ] as any[]).map(([label, key, type, opts, min]) => (
                <div key={key}>
                  <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1">{label}</div>
                  {type === 'select' ? (
                    <select
                      className="w-full bg-transparent border border-white/[0.08] rounded-xl px-3.5 py-2 text-white/70 text-sm focus:outline-none focus:border-brand/50 transition-all"
                      value={(replForm as any)[key]}
                      onChange={e => setReplForm(f => ({ ...f, [key]: e.target.value }))}
                    >
                      {opts.map((o: string) => <option key={o}>{o}</option>)}
                    </select>
                  ) : (
                    <input
                      className="w-full bg-transparent border border-white/[0.08] rounded-xl px-3.5 py-2 text-white font-mono text-sm focus:outline-none focus:border-brand/50 transition-all"
                      type="number"
                      step={type === 'decimal' ? '0.001' : '1'}
                      min={min ?? undefined}
                      value={(replForm as any)[key]}
                      onChange={e => setReplForm(f => ({ ...f, [key]: +e.target.value }))}
                    />
                  )}
                </div>
              ))}
            </div>

            <div className="flex gap-2 justify-end pt-1">
              <button
                className="px-4 py-2 rounded-lg text-[11px] text-white/25 hover:text-white/50 transition-colors"
                onClick={() => setReplModal(null)}
              >
                Cancel
              </button>
              <button
                className="px-4 py-2 border border-amber-400/20 rounded-lg text-[11px] text-amber-400/70 hover:border-amber-400/40 hover:text-amber-400 transition-all"
                onClick={() => queueReplacement(replModal)}
              >
                Queue Replacement
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// List sidebar
// ─────────────────────────────────────────────────────────────────────────────

type SessionFilter = 'running' | 'stopping' | 'starting' | 'all'

export default function AdminSessions() {
  const [list, setList]         = useState<any[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState<SessionFilter>('all')
  const [hideZero, setHideZero] = useState(false)

  useEffect(() => {
    let first = true
    const refresh = () =>
      api.admin.listSessions()
        .then((d: any) => {
          setList(d)
          if (first && d.length) { setSelected(d[0].session_id); first = false }
        })
        .catch(() => {})
        .finally(() => setLoading(false))

    refresh()
    const iv = setInterval(refresh, 5000)
    return () => clearInterval(iv)
  }, [])

  const filtered = list.filter((s: any) => {
    if (filter !== 'all' && s.health_state !== filter) return false
    if (hideZero && (s.total_volume ?? 0) <= 0) return false
    return true
  })

  return (
    <div className="flex flex-col md:flex-row h-full overflow-hidden">
      {/* Sidebar list */}
      <div className="w-full md:w-64 flex-shrink-0 border-b md:border-b-0 md:border-r border-white/[0.06] flex flex-col md:h-full max-h-64 md:max-h-none">
        {/* Header + filters */}
        <div className="border-b border-white/[0.06] bg-white/[0.01] shrink-0">
          <div className="px-4 py-2.5">
            <span className="text-[9px] font-semibold uppercase tracking-widest text-white/20">Sessions</span>
          </div>
          <div className="flex flex-wrap gap-1 px-3 pb-2">
            {(['all', 'running', 'stopping', 'starting'] as SessionFilter[]).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-2 py-0.5 rounded text-[9px] font-semibold uppercase tracking-widest transition-colors ${
                  filter === f
                    ? f === 'running'  ? 'bg-emerald-500/20 text-emerald-400/80'
                    : f === 'stopping' ? 'bg-white/10 text-white/60'
                    : f === 'starting' ? 'bg-yellow-500/20 text-yellow-400/80'
                    : 'bg-brand/20 text-brand/80'
                    : 'text-white/20 hover:text-white/40 hover:bg-white/[0.04]'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 px-3 pb-2.5">
            <button
              onClick={() => setHideZero(v => !v)}
              className={`flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-widest transition-colors ${
                hideZero ? 'text-brand/70' : 'text-white/20 hover:text-white/40'
              }`}
            >
              <span className={`w-3 h-3 rounded border flex items-center justify-center transition-colors ${
                hideZero ? 'border-brand/50 bg-brand/20' : 'border-white/15'
              }`}>
                {hideZero && <span className="w-1.5 h-1.5 rounded-sm bg-brand/80" />}
              </span>
              Hide zero vol
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="p-3 space-y-2">
              {[1,2,3].map(i => <div key={i} className="h-12 bg-white/[0.02] rounded-lg animate-pulse" />)}
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-4 py-6 text-[10px] text-white/20">No sessions</div>
          ) : (
            filtered.map((s: any) => (
              <button
                key={s.session_id}
                onClick={() => setSelected(s.session_id)}
                className={`w-full text-left px-3 py-3 border-b border-white/[0.04] transition-colors ${
                  selected === s.session_id
                    ? 'bg-brand/10 border-l-2 border-l-brand/50'
                    : 'hover:bg-white/[0.025]'
                }`}
              >
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotCls[s.health_state] ?? 'bg-white/15'}`} />
                  <span className={`text-[10px] font-semibold uppercase ${
                    s.health_state === 'running'    ? 'text-emerald-400/70' :
                    s.health_state === 'stopping'   ? 'text-yellow-400/70' :
                    s.health_state === 'starting'   ? 'text-yellow-400/70' :
                    s.health_state === 'recovering' ? 'text-pink-400/70' :
                    s.health_state === 'error'      ? 'text-red-400/70' :
                    s.health_state === 'stopped'    ? 'text-white/25' :
                    'text-white/30'
                  }`}>{s.health_state}</span>
                </div>
                <div className="text-[10px] font-mono text-white/30 truncate">{s.session_id.slice(0, 12)}</div>
                <div className="text-[9px] text-white/20 mt-0.5">{fmtUsd(s.gross_capital, 0)} · {fmtDate(s.started_at)}</div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Detail panel */}
      <div className="flex-1 overflow-y-auto p-5">
        {selected ? (
          <SessionDetail id={selected} />
        ) : (
          <div className="h-full flex items-center justify-center text-white/15 text-xs font-mono">
            Select a session
          </div>
        )}
      </div>
    </div>
  )
}
