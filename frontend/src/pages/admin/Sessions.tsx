import { useEffect, useState, useRef, useCallback } from 'react'
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

function fmtAge(isoTs: string | null): string {
  if (!isoTs) return '—'
  const ms = Date.now() - new Date(isoTs).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

function SessionDetail({ id }: { id: string }) {
  const addToast = useAppStore((s) => s.addToast)
  const [s, setS]               = useState<any>(null)
  const [stopModal, setStopModal] = useState(false)
  const [replModal, setReplModal] = useState<string | null>(null)
  const [openSlots, setOpenSlots] = useState<Record<string, boolean>>({})
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

  async function forceReplace(ssId: string) {
    try {
      await api.admin.forceReplaceSlot(id, ssId)
      addToast({ type: 'success', message: 'Force-replace triggered' })
      reload()
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
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
        {s.slots?.map((ss: any) => {
          const rt   = ss.runtime
          const lots = rt?.lot_list ?? []
          const buys = rt?.open_order_list ?? []
          const sells = rt?.sell_order_list ?? []
          const isOpen = openSlots[ss.session_slot_id]
          const hasSelling = lots.some((l: any) => l.status === 'SELLING')

          return (
            <div key={ss.session_slot_id} className="border-b border-white/[0.04] last:border-0">
              <button
                className="w-full flex items-start justify-between gap-3 px-4 py-3 hover:bg-white/[0.015] transition-colors text-left"
                onClick={() => setOpenSlots(p => ({ ...p, [ss.session_slot_id]: !p[ss.session_slot_id] }))}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                    <SlotStateChip state={ss.slot_state} />
                    <span className="text-[10px] text-white/20 font-mono">{ss.session_slot_id.slice(0, 8)}</span>
                    {rt?.question && <span className="text-[10px] text-white/35 truncate max-w-[200px]">{rt.question}</span>}
                  </div>
                  <div className="text-[10px] text-white/30">
                    {ss.allocation_pct}% · {fmtUsd(ss.allocated_capital)} alloc
                    {rt && <> · {fmtUsd(rt.deployed_capital)} deployed · <span className="text-white/40">{fmtUsd(rt.volume, 0)} vol</span> · pnl <span className={pnlColor(rt.realized_pnl)}>{fmtUsd(rt.realized_pnl)}</span></>}
                  </div>
                  {rt && (lots.length > 0 || buys.length + sells.length > 0) && (
                    <div className="flex items-center gap-2 mt-0.5">
                      {lots.length > 0 && <span className="text-[8px] font-mono text-white/20">{lots.length} pos</span>}
                      {(buys.length + sells.length) > 0 && <span className="text-[8px] font-mono text-white/20">{buys.length + sells.length} ord</span>}
                      {hasSelling && <span className="text-[8px] font-semibold text-amber-400/60 uppercase">selling</span>}
                      {rt.bid_price > 0 && <span className="text-[8px] font-mono text-white/25">bid {(rt.bid_price * 100).toFixed(1)}¢</span>}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {canStop && (
                    <>
                      <button
                        className="px-2.5 py-1 border border-orange-500/20 rounded-lg text-[10px] text-orange-400/50 hover:border-orange-500/40 hover:text-orange-400 transition-all"
                        onClick={e => { e.stopPropagation(); forceReplace(ss.session_slot_id) }}
                        title="Taker-sell all positions + cancel all orders on this market, then queue replacement"
                      >
                        Force↺
                      </button>
                      <button
                        className="px-2.5 py-1 border border-white/[0.08] rounded-lg text-[10px] text-white/30 hover:border-white/20 hover:text-white/55 transition-all"
                        onClick={e => { e.stopPropagation(); setReplModal(ss.session_slot_id) }}
                      >
                        Replace
                      </button>
                    </>
                  )}
                  <svg width="10" height="10" viewBox="0 0 10 10" className={`text-white/20 transition-transform duration-150 ${isOpen ? 'rotate-180' : ''}`}
                    fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                    <path d="M2 3.5l3 3 3-3" />
                  </svg>
                </div>
              </button>

              {/* Expanded: positions + orders */}
              {isOpen && (
                <div className="bg-white/[0.012] border-t border-b border-white/[0.04] mx-3 mb-1 rounded-xl overflow-hidden">
                  {lots.length > 0 && (() => {
                    const groups = new Map<string, any[]>()
                    for (const lot of lots) {
                      const key = `${(lot.entry_price ?? 0).toFixed(4)}|${(lot.target_sell_price ?? 0).toFixed(4)}`
                      if (!groups.has(key)) groups.set(key, [])
                      groups.get(key)!.push(lot)
                    }
                    return (
                      <div>
                        <div className="px-3 pt-2 pb-1 text-[8px] font-semibold uppercase tracking-widest text-white/20">Positions</div>
                        {Array.from(groups.entries()).map(([key, gLots]) => {
                          const rep = gLots[0]
                          const totalSize = gLots.reduce((a: number, l: any) => a + l.size, 0)
                          const totalTokens = gLots.reduce((a: number, l: any) => a + (l.tokens ?? 0), 0)
                          const isSell = gLots.some((l: any) => l.status === 'SELLING')
                          const entryVal = rep.entry_price * totalTokens
                          const curVal = rep.current_price > 0 ? rep.current_price * totalTokens : 0
                          const pnlPct = entryVal > 0 && curVal > 0 ? ((curVal - entryVal) / entryVal) * 100 : null
                          const ts = gLots.map((l: any) => l.timestamp_entry).filter(Boolean).sort()[0] ?? null
                          return (
                            <div key={key} className="flex items-center gap-2 px-3 py-1.5 border-t border-white/[0.03]">
                              <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                                <span className="text-[10px] font-mono text-white/55">${totalSize.toFixed(2)}</span>
                                {gLots.length > 1 && <span className="text-[8px] font-mono text-white/25">×{gLots.length}</span>}
                                <span className="text-[9px] font-mono text-white/25">
                                  {(rep.entry_price * 100).toFixed(1)}¢ → {(rep.target_sell_price * 100).toFixed(1)}¢
                                </span>
                                {isSell && <span className="text-[8px] font-semibold text-amber-400/60 uppercase">selling</span>}
                              </div>
                              <div className="shrink-0 flex items-center gap-2">
                                {pnlPct !== null && (
                                  <span className={`text-[9px] font-mono ${pnlPct >= 0 ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
                                    {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%
                                  </span>
                                )}
                                <span className="text-[8px] font-mono text-white/20">{fmtAge(ts)}</span>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )
                  })()}

                  {(sells.length > 0 || buys.length > 0) && (
                    <div>
                      <div className="px-3 pt-2 pb-1 text-[8px] font-semibold uppercase tracking-widest text-white/20">Orders</div>
                      {[...sells, ...buys].map((o: any) => {
                        const isSell = o.side === 'SELL'
                        return (
                          <div key={o.order_id} className="flex items-center gap-2 px-3 py-1.5 border-t border-white/[0.03]">
                            <span className={`text-[8px] font-bold uppercase tracking-widest px-1 py-0.5 rounded shrink-0 ${
                              isSell ? 'bg-red-500/10 text-red-400/60' : 'bg-emerald-500/10 text-emerald-400/60'
                            }`}>{isSell ? 'TP' : 'BUY'}</span>
                            <span className="text-[10px] font-mono text-white/50 flex-1">{(o.price * 100).toFixed(1)}¢</span>
                            <span className="text-[9px] font-mono text-white/25 shrink-0">${o.size.toFixed(2)}</span>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  {lots.length === 0 && sells.length === 0 && buys.length === 0 && (
                    <div className="px-3 py-3 text-[10px] font-mono text-white/15">No activity</div>
                  )}

                  {ss.market_history?.length > 0 && (
                    <div className="px-3 pt-2 pb-2 border-t border-white/[0.03]">
                      <div className="text-[8px] font-semibold uppercase tracking-widest text-white/20 mb-1">Market history</div>
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
              )}
            </div>
          )
        })}
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
// Admin Launch Session Modal
// ─────────────────────────────────────────────────────────────────────────────

function AdminLaunchModal({ onClose, onDone }: { onClose: () => void; onDone: (sessionId: string) => void }) {
  const addToast = useAppStore(s => s.addToast)
  const overlayRef = useRef<HTMLDivElement>(null)

  const [users, setUsers]         = useState<any[]>([])
  const [loadingUsers, setLoadingUsers] = useState(true)
  const [selectedUser, setSelectedUser] = useState<any | null>(null)
  const [capital, setCapital]     = useState('')
  const [stopMode, setStopMode]   = useState<'days' | 'volume'>('days')
  const [stopDays, setStopDays]   = useState('30')
  const [targetVol, setTargetVol] = useState('')
  const [slPct, setSlPct]         = useState('20')
  const [launching, setLaunching] = useState(false)
  const [error, setError]         = useState('')

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  useEffect(() => {
    api.admin.listUsers(200).then((d: any) => {
      // Only users without active session
      const eligible = d.filter((u: any) => !u.active_session_id && u.wallet_id)
      setUsers(eligible)
    }).catch(() => {}).finally(() => setLoadingUsers(false))
  }, [])

  async function launch() {
    if (!selectedUser || !capital) return
    const gross = parseFloat(capital)
    if (isNaN(gross) || gross <= 0) { setError('Invalid capital'); return }
    setLaunching(true)
    setError('')
    try {
      const res: any = await api.admin.launchForUser({
        user_id:       selectedUser.id,
        wallet_id:     selectedUser.wallet_id,
        gross_capital: gross,
        stop_mode:     stopMode,
        stop_days:     stopMode === 'days' ? parseInt(stopDays) : null,
        target_volume: stopMode === 'volume' ? parseFloat(targetVol) : null,
        session_sl_pct: parseFloat(slPct) || 20,
      })
      addToast({ type: 'success', message: `Session launched for ${selectedUser.display_name ?? selectedUser.id.slice(0, 8)}` })
      onDone(res.session_id)
    } catch (e: any) {
      setError(e.message ?? 'Launch failed')
      setLaunching(false)
    }
  }

  const canLaunch = selectedUser && capital && parseFloat(capital) > 0 && !launching

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4 py-6"
      onClick={e => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className="bg-[#0f1012] border border-white/[0.08] rounded-2xl w-full max-w-md shadow-2xl overflow-hidden">
        <div className="flex items-center justify-between px-5 pt-5 pb-4 border-b border-white/[0.05]">
          <span className="text-sm font-semibold text-white/80">Launch Session</span>
          <button className="w-6 h-6 flex items-center justify-center rounded-lg text-white/20 hover:text-white/55 hover:bg-white/[0.06] transition-all" onClick={onClose}>✕</button>
        </div>

        <div className="p-5 space-y-4">
          {/* User picker */}
          <div>
            <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">User (no active session)</div>
            {loadingUsers ? (
              <div className="h-10 bg-white/[0.03] rounded-xl animate-pulse" />
            ) : users.length === 0 ? (
              <div className="text-[11px] text-white/30 px-3 py-2.5 border border-white/[0.07] rounded-xl">All users have active sessions</div>
            ) : (
              <select
                className="w-full bg-[#0a0b0d] border border-white/[0.08] rounded-xl px-3.5 py-2.5 text-white/70 text-[12px] focus:outline-none focus:border-brand/50 transition-all"
                value={selectedUser?.id ?? ''}
                onChange={e => {
                  const u = users.find(u => u.id === e.target.value)
                  setSelectedUser(u ?? null)
                  if (u?.trading_balance) setCapital(String(Math.floor(u.trading_balance * 100) / 100))
                }}
              >
                <option value="">Select a user…</option>
                {users.map((u: any) => (
                  <option key={u.id} value={u.id}>
                    {u.display_name ?? u.id.slice(0, 12)} — ${(u.trading_balance ?? 0).toFixed(2)}
                  </option>
                ))}
              </select>
            )}
          </div>

          {selectedUser && (
            <>
              <div className="grid grid-cols-2 gap-3">
                {/* Capital */}
                <div>
                  <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">Capital (USDC)</div>
                  <input
                    className="w-full bg-transparent border border-white/[0.08] rounded-xl px-3.5 py-2.5 text-white font-mono text-sm placeholder:text-white/15 focus:outline-none focus:border-brand/50 transition-all"
                    type="number" min="0" step="1" placeholder="100"
                    value={capital}
                    onChange={e => setCapital(e.target.value)}
                  />
                  {selectedUser.trading_balance != null && (
                    <div className="text-[9px] text-white/25 mt-1">Balance: ${selectedUser.trading_balance.toFixed(2)}</div>
                  )}
                </div>
                {/* SL */}
                <div>
                  <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">Session SL %</div>
                  <input
                    className="w-full bg-transparent border border-white/[0.08] rounded-xl px-3.5 py-2.5 text-white font-mono text-sm placeholder:text-white/15 focus:outline-none focus:border-brand/50 transition-all"
                    type="number" min="1" max="100" step="1" placeholder="20"
                    value={slPct}
                    onChange={e => setSlPct(e.target.value)}
                  />
                </div>
              </div>

              {/* Stop mode */}
              <div>
                <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">Stop Mode</div>
                <div className="flex gap-2">
                  {(['days', 'volume'] as const).map(m => (
                    <button
                      key={m}
                      onClick={() => setStopMode(m)}
                      className={`flex-1 py-2 rounded-xl text-[11px] font-semibold border transition-colors ${
                        stopMode === m
                          ? 'border-brand/40 bg-brand/10 text-brand/80'
                          : 'border-white/[0.07] text-white/30 hover:border-white/15 hover:text-white/55'
                      }`}
                    >
                      {m === 'days' ? 'By Days' : 'By Volume'}
                    </button>
                  ))}
                </div>
              </div>

              {stopMode === 'days' ? (
                <div>
                  <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">Duration (days)</div>
                  <input
                    className="w-full bg-transparent border border-white/[0.08] rounded-xl px-3.5 py-2.5 text-white font-mono text-sm placeholder:text-white/15 focus:outline-none focus:border-brand/50 transition-all"
                    type="number" min="1" step="1" placeholder="30"
                    value={stopDays}
                    onChange={e => setStopDays(e.target.value)}
                  />
                </div>
              ) : (
                <div>
                  <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">Target Volume (USDC)</div>
                  <input
                    className="w-full bg-transparent border border-white/[0.08] rounded-xl px-3.5 py-2.5 text-white font-mono text-sm placeholder:text-white/15 focus:outline-none focus:border-brand/50 transition-all"
                    type="number" min="0" step="100" placeholder="10000"
                    value={targetVol}
                    onChange={e => setTargetVol(e.target.value)}
                  />
                </div>
              )}
            </>
          )}

          {error && <div className="text-[11px] text-red-400 bg-red-500/5 border border-red-500/15 rounded-xl px-3 py-2">{error}</div>}
        </div>

        <div className="px-5 pb-5 flex justify-end gap-2">
          <button
            className="px-4 py-2 rounded-lg text-[11px] text-white/25 hover:text-white/50 transition-colors"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            disabled={!canLaunch}
            onClick={launch}
            className="px-4 py-2 border border-emerald-500/25 rounded-lg text-[11px] text-emerald-400/70 hover:border-emerald-500/50 hover:text-emerald-400 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {launching ? 'Launching…' : 'Launch Session'}
          </button>
        </div>
      </div>
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
  const [launchModal, setLaunchModal] = useState(false)

  const refresh = useCallback(() =>
    api.admin.listSessions()
      .then((d: any) => setList(d))
      .catch(() => {})
      .finally(() => setLoading(false)),
  [])

  useEffect(() => {
    let first = true
    const doRefresh = () =>
      api.admin.listSessions()
        .then((d: any) => {
          setList(d)
          if (first && d.length) { setSelected(d[0].session_id); first = false }
        })
        .catch(() => {})
        .finally(() => setLoading(false))

    doRefresh()
    const iv = setInterval(doRefresh, 5000)
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
          <div className="px-4 py-2.5 flex items-center justify-between">
            <span className="text-[9px] font-semibold uppercase tracking-widest text-white/20">Sessions</span>
            <button
              onClick={() => setLaunchModal(true)}
              className="px-2.5 py-1 border border-emerald-500/20 rounded-lg text-[9px] font-semibold text-emerald-400/60 hover:border-emerald-500/40 hover:text-emerald-400 transition-all uppercase tracking-widest"
            >
              + Launch
            </button>
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
                <div className="text-[10px] font-mono text-white/30 truncate">{s.username ?? s.session_id.slice(0, 12)}</div>
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

      {launchModal && (
        <AdminLaunchModal
          onClose={() => setLaunchModal(false)}
          onDone={(sessionId) => {
            setLaunchModal(false)
            refresh()
            setTimeout(() => setSelected(sessionId), 500)
          }}
        />
      )}
    </div>
  )
}
