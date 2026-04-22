/**
 * RunTab  active session view or launch form.
 */
import { useState, useEffect, useMemo, useRef } from 'react'
import type { AppUser, Strategy, Session, RuntimeSlotStats } from '../../types'
import { api } from '../../utils/api'
import { useAppStore } from '../../store'
import { fmtUsd, fmtUsdFloor, fmtDuration, pnlColor } from '../../utils/format'
import { useSessionWebSocket } from '../../hooks/useWebSocket'
import { InfoTip } from '../ui/Tooltip'

interface Props {
  user: AppUser
  strategy: Strategy | null
  activeSession: Session | null
  loading: boolean
  usdcBalance: number | null
  onSessionChange: (s: Session | null) => void
}

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

function fmtDur(hours: number): string {
  if (!isFinite(hours) || hours <= 0) return '-'
  const m = Math.round(hours * 60)
  const w = Math.floor(m / (7 * 24 * 60))
  const d = Math.floor((m % (7 * 24 * 60)) / (24 * 60))
  const h = Math.floor((m % (24 * 60)) / 60)
  const min = m % 60
  if (w > 0) return `${w}w ${d}d`
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${min}m`
  return `${min}m`
}

/** Uppercase micro-label */
function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="block text-[10px] text-white/30 uppercase tracking-widest font-semibold leading-none">
      {children}
    </span>
  )
}

/** Inline number input with optional suffix badge */
function Field({
  value, onChange, placeholder, suffix, integer = false, error = false,
}: {
  value: string; onChange: (v: string) => void; placeholder: string
  suffix?: string; integer?: boolean; error?: boolean
}) {
  const re = integer ? /^\d*$/ : /^\d*\.?\d*$/
  return (
    <div className="relative w-full">
      <input
        className={[
          'w-full bg-transparent border rounded-xl px-3.5 py-2.5',
          'text-white font-mono text-sm placeholder:text-white/15',
          'focus:outline-none transition-all',
          suffix ? 'pr-14' : '',
          error
            ? 'border-red-500/35 focus:border-red-500/55'
            : 'border-white/[0.08] hover:border-white/15 focus:border-brand/50',
        ].join(' ')}
        type="text"
        inputMode={integer ? 'numeric' : 'decimal'}
        placeholder={placeholder}
        value={value}
        onChange={e => { const v = e.target.value; if (re.test(v)) onChange(v) }}
      />
      {suffix && (
        <span className="absolute right-3.5 top-1/2 -translate-y-1/2 text-[10px] text-white/22 pointer-events-none select-none">
          {suffix}
        </span>
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Slot card (per-market)
// -----------------------------------------------------------------------------

/** Fetch market images for a set of conditionIds; returns a stable map. */
function useMarketImages(conditionIds: (string | null)[]): Record<string, string> {
  const [imgs, setImgs] = useState<Record<string, string>>({})
  const ids = conditionIds.filter(Boolean) as string[]
  const key = ids.sort().join(',')

  useEffect(() => {
    if (!ids.length) return
    const missing = ids.filter(id => !(id in imgs))
    if (!missing.length) return

    const API_BASE = import.meta.env.VITE_API_URL
    Promise.all(
      missing.map(id =>
        fetch(`${API_BASE}/api/v1/user/strategy/market-image?conditionId=${encodeURIComponent(id)}`)
          .then(r => r.json())
          .then((data: any) => ({ id, url: data.image || '' }))
          .catch(() => ({ id, url: '' }))
      )
    ).then(results => {
      const patch: Record<string, string> = {}
      results.forEach(({ id, url }) => { if (url) patch[id] = url })
      if (Object.keys(patch).length) setImgs(prev => ({ ...prev, ...patch }))
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return imgs
}

// -----------------------------------------------------------------------------
// Positions + Open Orders flat lists
// -----------------------------------------------------------------------------

function fmtAge(isoTs: string | null): string {
  if (!isoTs) return '-'
  const ms = Date.now() - new Date(isoTs).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

function MarketThumb({ img, question }: { img: string | null; question: string | null }) {
  return (
    <div className="w-8 h-8 rounded-lg shrink-0 bg-white/[0.04] overflow-hidden flex items-center justify-center">
      {img
        ? <img src={img} alt="" className="w-full h-full object-cover" />
        : <span className="text-[10px] text-white/15 font-mono">?</span>}
    </div>
  )
}

// -- Slot-first collapsed activity (Option A) ----------------------------------

function SlotActivityList({ slots, loaded }: { slots: RuntimeSlotStats[]; loaded: boolean }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const toggle = (id: string) => setOpen(p => ({ ...p, [id]: !p[id] }))

  const condIds = useMemo(() =>
    slots.map(s => s.condition_id).filter(Boolean) as string[]
  , [slots])
  const imgs = useMarketImages(condIds)

  const totalPos = useMemo(() => slots.reduce((a, s) => a + (s.lot_list?.length ?? 0), 0), [slots])
  const totalOrd = useMemo(() => slots.reduce((a, s) => a + (s.open_order_list?.length ?? 0) + (s.sell_order_list?.length ?? 0), 0), [slots])

  return (
    <div className="border-b border-white/[0.03]">
      {/* Section header */}
      <div className="px-5 py-2.5 flex items-center gap-3">
        <span className="text-[9px] text-white/20 uppercase tracking-widest font-semibold flex-1">Slots</span>
        {loaded && (
          <span className="text-[9px] font-mono text-white/15">{totalPos} pos · {totalOrd} orders</span>
        )}
      </div>

      {!loaded ? (
        <div className="px-5 pb-3 space-y-2">
          {[1,2,3].map(i => <div key={i} className="h-12 rounded-xl bg-white/[0.02] animate-pulse" />)}
        </div>
      ) : slots.length === 0 ? (
        <div className="px-5 pb-3 text-[10px] font-mono text-white/15">No slots</div>
      ) : (
        <div className="pb-1">
          {slots.map(s => {
            const lots   = s.lot_list ?? []
            const sells  = s.sell_order_list ?? []
            const buys   = s.open_order_list ?? []
            const isOpen = open[s.session_slot_id]
            const img    = s.condition_id ? (imgs[s.condition_id] ?? null) : null
            const upnl   = s.unrealized_pnl
            const hasSelling = lots.some(l => l.status === 'SELLING')

            return (
              <div key={s.session_slot_id}>
                {/* Slot summary row  always visible, tap to expand */}
                <button
                  className="w-full flex items-center gap-3 px-5 py-2.5 hover:bg-white/[0.02] transition-colors text-left"
                  onClick={() => toggle(s.session_slot_id)}
                >
                  <MarketThumb img={img} question={s.question} />

                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] text-white/65 truncate leading-tight">
                      {s.question ?? 'Idle'}
                    </div>
                    {/* Compact chips */}
                    <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                      <span className={`text-[8px] font-semibold uppercase tracking-widest px-1 py-0.5 rounded ${
                        s.state === 'active' ? 'bg-emerald-500/10 text-emerald-400/70'
                        : s.state === 'replacement_queued' ? 'bg-amber-500/10 text-amber-400/70'
                        : 'bg-white/[0.04] text-white/25'
                      }`}>{s.state}</span>
                      {lots.length > 0 && (
                        <span className="text-[8px] font-mono text-white/30">{lots.length} pos</span>
                      )}
                      {(buys.length + sells.length) > 0 && (
                        <span className="text-[8px] font-mono text-white/25">{buys.length + sells.length} ord</span>
                      )}
                      {hasSelling && (
                        <span className="text-[8px] font-semibold text-amber-400/60 uppercase tracking-widest">selling</span>
                      )}
                    </div>
                  </div>

                  {/* Right summary */}
                  <div className="shrink-0 text-right flex flex-col items-end gap-0.5">
                    <span className="text-[11px] font-mono text-white/50">${s.deployed_capital.toFixed(2)}</span>
                    {upnl !== 0 && (
                      <span className={`text-[9px] font-mono ${upnl >= 0 ? 'text-emerald-400/60' : 'text-red-400/60'}`}>
                        {upnl >= 0 ? '+' : ''}{fmtUsd(upnl)}
                      </span>
                    )}
                    <svg
                      width="10" height="10" viewBox="0 0 10 10"
                      className={`text-white/20 transition-transform duration-150 ${isOpen ? 'rotate-180' : ''}`}
                      fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
                    >
                      <path d="M2 3.5l3 3 3-3" />
                    </svg>
                  </div>
                </button>

                {/* Expanded detail */}
                {isOpen && (
                  <div className="bg-white/[0.015] border-t border-b border-white/[0.04] mx-3 mb-1 rounded-xl overflow-hidden">
                    {/* Positions */}
                    {lots.length > 0 && (
                      <div>
                        <div className="px-3 pt-2 pb-1 text-[8px] font-semibold uppercase tracking-widest text-white/20">Positions</div>
                        {lots.map((lot: any) => {
                          const age = fmtAge(lot.timestamp_entry)
                          const entryVal = lot.entry_price * lot.tokens
                          const currentVal = lot.current_price > 0 ? lot.current_price * lot.tokens : 0
                          const pnlPct = entryVal > 0 && currentVal > 0 ? ((currentVal - entryVal) / entryVal) * 100 : null
                          return (
                            <div key={lot.lot_id} className="flex items-center gap-2 px-3 py-1.5 border-t border-white/[0.03]">
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="text-[10px] font-mono text-white/55">${lot.size.toFixed(2)}</span>
                                  <span className="text-[9px] font-mono text-white/25">{(lot.entry_price * 100).toFixed(1)}¢ → {(lot.target_sell_price * 100).toFixed(1)}¢</span>
                                  {lot.status === 'SELLING' && <span className="text-[8px] font-semibold text-amber-400/60 uppercase">selling</span>}
                                </div>
                              </div>
                              <div className="shrink-0 flex items-center gap-2">
                                {pnlPct !== null && (
                                  <span className={`text-[9px] font-mono ${pnlPct >= 0 ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
                                    {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%
                                  </span>
                                )}
                                <span className="text-[8px] font-mono text-white/20">{age}</span>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                    {/* Orders */}
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
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Stop modal
// -----------------------------------------------------------------------------

function StopModal({ onStop, onClose, stopping, isStopping }: {
  onStop: (mode: 'soft' | 'hard') => void
  onClose: () => void
  stopping: boolean
  isStopping: boolean
}) {
  const overlayRef = useRef<HTMLDivElement>(null)

  // Close on backdrop click
  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose()
  }

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm px-4"
      onClick={handleOverlayClick}
    >
      <div className="bg-[#0f1012] border border-white/[0.08] rounded-2xl w-full max-w-sm shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 pt-5 pb-4 border-b border-white/[0.05]">
          <span className="text-sm font-semibold text-white/80">Stop Session</span>
          <button
            className="w-6 h-6 flex items-center justify-center rounded-lg text-white/20 hover:text-white/55 hover:bg-white/[0.06] transition-all"
            onClick={onClose}
            aria-label="Close"
          >
            ?
          </button>
        </div>

        {/* Options */}
        <div className="p-5 space-y-3">
          {/* Soft stop  hidden if already stopping */}
          {!isStopping && (
            <button
              className="w-full text-left p-4 border border-white/[0.07] rounded-xl hover:border-white/14 hover:bg-white/[0.025] transition-all group disabled:opacity-40"
              disabled={stopping}
              onClick={() => onStop('soft')}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[13px] font-semibold text-white/70 group-hover:text-white/85 transition-colors">Soft Stop</span>
                <span className="text-[9px] font-mono text-white/20 uppercase tracking-widest">Recommended</span>
              </div>
              <p className="text-[11px] text-white/30 leading-relaxed">
                Stops new entries. Open positions continue until they hit their target price or stop-loss. Capital is recovered gradually.
              </p>
            </button>
          )}

          {/* Hard stop */}
          <button
            className="w-full text-left p-4 border border-red-500/12 rounded-xl hover:border-red-500/25 hover:bg-red-500/[0.04] transition-all group disabled:opacity-40"
            disabled={stopping}
            onClick={() => onStop('hard')}
          >
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[13px] font-semibold text-red-400/60 group-hover:text-red-400/85 transition-colors">Hard Stop</span>
              <span className="text-[9px] font-mono text-red-400/30 uppercase tracking-widest">Immediate</span>
            </div>
            <p className="text-[11px] text-white/25 leading-relaxed">
              Cancels all open orders and market-sells all positions immediately. May result in worse exit prices.
            </p>
          </button>
        </div>

        {stopping && (
          <div className="px-5 pb-5 text-center text-[11px] font-mono text-white/20">Stopping...</div>
        )}
      </div>
    </div>
  )
}

// -----------------------------------------------------------------------------
// -----------------------------------------------------------------------------
// Session launch loader (bridge-style with dash connector)
// -----------------------------------------------------------------------------

type StepState = 'pending' | 'current' | 'done' | 'error'

interface Step { label: string; state: StepState; errorMsg?: string }

function SessionLoader({
  steps,
  subtitle,
  title,
  error,
  onRetry,
}: {
  steps: Step[]
  subtitle?: string
  title?: string
  error?: string | null
  onRetry?: () => void
}) {
  return (
    <div className="flex flex-col items-center justify-center px-8 py-14 gap-8">

      {/* Title */}
      {title && (
        <div className="text-center">
          <div className="text-[13px] font-medium text-white/60 leading-snug">{title}</div>
        </div>
      )}

      {/* Steps with dash connector */}
      <div className="w-full max-w-[230px]">
        {steps.map((s, i) => (
          <div key={i}>
            {/* Step row */}
            <div className={`flex items-center gap-3 transition-all duration-400 ${
              s.state === 'pending' ? 'opacity-18' : 'opacity-100'
            }`}>
              {/* Icon */}
              {s.state === 'done' ? (
                <span className="flex items-center justify-center w-5 h-5 rounded-full bg-emerald-500/15 flex-shrink-0">
                  <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                    <path d="M2 5l2.5 2.5L8 3" stroke="#10b981" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </span>
              ) : s.state === 'error' ? (
                <span className="flex items-center justify-center w-5 h-5 rounded-full bg-red-500/15 flex-shrink-0">
                  <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                    <path d="M2 2l6 6M8 2L2 8" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                </span>
              ) : s.state === 'current' ? (
                <span className="flex items-center justify-center w-5 h-5 flex-shrink-0">
                  <svg className="animate-spin" width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <circle cx="8" cy="8" r="6" stroke="#facc15" strokeWidth="2" strokeOpacity="0.2"/>
                    <path d="M8 2a6 6 0 0 1 6 6" stroke="#facc15" strokeWidth="2" strokeLinecap="round"/>
                  </svg>
                </span>
              ) : (
                <span className="flex items-center justify-center w-5 h-5 flex-shrink-0">
                  <div className="w-1.5 h-1.5 rounded-full bg-white/15" />
                </span>
              )}

              {/* Label + error */}
              <div className="flex-1 min-w-0">
                <span className={`text-[11px] font-medium leading-none transition-colors ${
                  s.state === 'done'    ? 'text-white/35' :
                  s.state === 'error'  ? 'text-red-400/80' :
                  s.state === 'current' ? 'text-white/80' :
                                          'text-white/18'
                }`}>
                  {s.label}
                </span>
                {s.state === 'error' && s.errorMsg && (
                  <div className="text-[9.5px] text-red-400/50 mt-0.5 leading-snug">{s.errorMsg}</div>
                )}
              </div>
            </div>

            {/* Dash connector  between steps, not after last */}
            {i < steps.length - 1 && (
              <div className="ml-[9px] flex flex-col gap-[3px] py-[4px]">
                {[0,1,2].map(d => (
                  <div key={d} className={`w-px h-[3px] rounded-full transition-colors ${
                    s.state === 'done' ? 'bg-emerald-500/25' : 'bg-white/[0.06]'
                  }`} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {subtitle && !error && (
        <div className="text-[9px] font-mono text-white/12 tracking-widest">{subtitle}</div>
      )}

      {onRetry && error && (
        <button
          className="text-[11px] text-white/30 hover:text-white/60 border border-white/[0.07] hover:border-white/18 px-4 py-1.5 rounded-lg transition-all"
          onClick={onRetry}
        >
          Try again
        </button>
      )}
    </div>
  )
}

// -- SessionStartLoader  single loader driven by real backend state ------------
//
// phase='api'      API call in flight (balance/capital checks); error shown here if rejected
// phase='starting'  session exists, WS-driven: healthState + rt advance steps 1-4

const SESSION_START_STEPS = [
  'Checking balances...',
  'Connecting to Polymarket...',
  'Configuring slots...',
  'Placing first orders...',
  'Session live',
]

// Fake delays (ms) for steps 1?2?3 in 'starting' phase  real WS takes over if faster
const FAKE_STEP_DELAYS = [800, 1200, 1000]

function SessionStartLoader({
  sessionId,
  healthState,
  rt,
  error,
  onRetry,
  displayName,
  phase = 'starting',
}: {
  sessionId?: string
  healthState?: string
  rt?: any
  error?: string | null
  onRetry?: () => void
  displayName?: string
  phase?: 'api' | 'starting'
}) {
  const [fakeStep, setFakeStep] = useState(0)

  // In 'starting' phase, auto-advance fakeStep with short delays so UI feels alive.
  // Real WS signals always win (realStep >= fakeStep).
  useEffect(() => {
    if (phase !== 'starting') return
    if (fakeStep >= FAKE_STEP_DELAYS.length) return
    const t = setTimeout(() => setFakeStep(s => s + 1), FAKE_STEP_DELAYS[fakeStep])
    return () => clearTimeout(t)
  }, [phase, fakeStep])

  // Reset fake step when phase changes (e.g. api ? starting)
  useEffect(() => {
    if (phase === 'starting') setFakeStep(0)
  }, [phase])

  const realStep = (() => {
    if (phase === 'api') return 0
    if (healthState === 'running') return 4
    if (rt && (rt.order_count > 0 || rt.trade_count > 0)) return 3
    if (rt) return 2
    if (healthState === 'starting') return 1
    return 0
  })()

  // Real signal always wins; fake only used when real is behind
  const step = Math.max(realStep, fakeStep)

  const steps: Step[] = SESSION_START_STEPS.map((label, i) => ({
    label,
    state: error && i === step
      ? 'error'
      : i < step  ? 'done'
      : i === step ? 'current'
      : 'pending',
    errorMsg: error && i === step ? error : undefined,
  }))

  const title = displayName
    ? `${displayName}, launching your session...`
    : 'Launching your session...'

  return (
    <SessionLoader
      steps={steps}
      title={title}
      subtitle={sessionId ? `#${sessionId.slice(0, 8)}` : undefined}
      error={error}
      onRetry={onRetry}
    />
  )
}

// -----------------------------------------------------------------------------
// Active session
// -----------------------------------------------------------------------------

const dotCls: Record<string, string> = {
  running: 'bg-emerald-400', starting: 'bg-yellow-400 animate-pulse',
  recovering: 'bg-orange-400 animate-pulse', stopping: 'bg-white/30 animate-pulse',
  degraded: 'bg-red-500', error: 'bg-red-500', stopped: 'bg-white/20', idle: 'bg-white/20',
}

function MiniStat({ label, value, color, tooltip, tooltipTitle, sub }: {
  label: string; value: React.ReactNode; color?: string; tooltip?: string; tooltipTitle?: string; sub?: React.ReactNode
}) {
  return (
    <div>
      <div className="flex items-center gap-0.5 mb-0.5">
        <span className="text-[9px] text-white/20 uppercase tracking-widest font-semibold">{label}</span>
        {tooltip && <InfoTip content={tooltip} title={tooltipTitle} />}
      </div>
      <div className={`text-[12px] font-bold font-mono tabular-nums leading-none ${color ?? 'text-white/70'}`}>{value}</div>
      {sub && <div className="text-[9px] font-mono text-white/25 mt-0.5 leading-tight">{sub}</div>}
    </div>
  )
}

function Sk({ w = 'w-16', h = 'h-3' }: { w?: string; h?: string }) {
  return <span className={`inline-block ${w} ${h} rounded bg-white/[0.06] animate-pulse`} />
}

function ActiveSession({ session, onStopped, displayName }: { session: Session; onStopped: () => void; displayName?: string }) {
  const addToast  = useAppStore(s => s.addToast)
  const slotStats = useAppStore(s => s.slotStats)
  const [stopping, setStopping]     = useState(false)
  const [showStopModal, setShowStopModal] = useState(false)

  useSessionWebSocket(session.session_id)

  const rt            = session.runtime
  const [everLoaded, setEverLoaded] = useState(!!rt)
  useEffect(() => { if (rt) setEverLoaded(true) }, [!!rt])
  const loaded        = everLoaded
  const liveStatus    = rt?.status ?? session.status
  const healthState   = rt?.health_state ?? session.health_state
  const realizedPnl   = rt?.realized_pnl ?? 0
  const unrealizedPnl = rt?.unrealized_pnl ?? 0
  const fees          = rt?.paid_fees ?? 0
  const floorFees     = Math.floor(fees * 100) / 100
  const netPnl        = realizedPnl + unrealizedPnl - floorFees
  const isStopping    = ['stopping'].includes(liveStatus) || healthState === 'stopping'
  const canStop       = !['stopped', 'error'].includes(liveStatus)
  const isStarting    = ['starting', 'pending'].includes(liveStatus) || ['starting'].includes(healthState)

  // React to live status from WS  onStopped when terminal
  useEffect(() => {
    if (['stopped', 'error'].includes(liveStatus)) {
      onStopped()
    }
  }, [liveStatus])

  const progressPct = (() => {
    if (!rt) return null
    if (session.stop_mode === 'volume' && session.target_volume)
      return Math.min(100, (rt.total_volume / session.target_volume) * 100)
    if (session.stop_mode === 'days' && session.stop_days && session.started_at) {
      const elapsed = (Date.now() - new Date(session.started_at).getTime()) / 1000
      return Math.min(100, (elapsed / (session.stop_days * 86400)) * 100)
    }
    return 0
  })()

  const progressLabel = session.stop_mode === 'days'
    ? (() => {
        if (!session.started_at || !session.stop_days) return `${session.stop_days ?? '?'}d`
        const elapsed = (Date.now() - new Date(session.started_at).getTime()) / 1000
        const remaining = (session.stop_days * 86400) - elapsed
        const rd = Math.floor(remaining / 86400)
        const rh = Math.floor((remaining % 86400) / 3600)
        return remaining > 0 ? `${rd > 0 ? `${rd}d ` : ''}${rh}h left` : 'Complete'
      })()
    : rt ? `${fmtUsd(rt.total_volume, 0)} / ${fmtUsd(session.target_volume ?? 0, 0)}` : null

  async function stop(mode: 'soft' | 'hard') {
    setStopping(true); setShowStopModal(false)
    try {
      await api.stopSession(session.session_id, mode)
      addToast({ type: 'info', message: mode === 'soft' ? 'Soft stop initiated · waiting for positions to close' : 'Hard stop executed' })
      // Hard stop: backend returns quickly, WS will push 'stopped'
      // Soft stop: let WS drive  do NOT call onStopped here, session stays visible as 'stopping'
      if (mode === 'hard') {
        // Give WS a moment; fallback poll
        setTimeout(() => {
          api.getActiveSession().then((s: any) => {
            if (!s || ['stopped','error'].includes(s.status)) onStopped()
          }).catch(() => {})
        }, 3000)
      }
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setStopping(false) }
  }

  // Show unified loader while session is booting
  if (isStarting) {
    return (
      <SessionStartLoader
        sessionId={session.session_id}
        healthState={healthState}
        rt={rt}
        displayName={displayName}
      />
    )
  }

  return (
    <div className="flex flex-col gap-0">

      {/* -- Status bar ------------------------------------------------------- */}
      <div className="px-5 pt-4 pb-3 flex items-center justify-between border-b border-white/[0.04]">
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 transition-colors duration-500 ${dotCls[healthState] ?? 'bg-white/20'}`} />
          <span className="text-[10px] font-semibold uppercase tracking-widest text-white/45 transition-all duration-300">
            {healthState}
          </span>
          <span className="text-[10px] font-mono text-white/18">
            {loaded ? `· ${fmtDuration(rt!.uptime_s)}` : ''}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[9px] font-mono text-white/15">#{session.session_id.slice(0, 8)}</span>
          {canStop && (
            <button
              className="px-2.5 py-1 rounded-lg border border-white/[0.08] text-[10px] text-white/30 hover:border-white/18 hover:text-white/55 transition-all disabled:opacity-30"
              disabled={stopping}
              onClick={() => setShowStopModal(true)}
            >
              {stopping ? '...' : 'Stop'}
            </button>
          )}
        </div>
      </div>

      {/* -- Stopping banner ------------------------------------------------- */}
      {isStopping && (
        <div className="mx-5 mt-3 px-4 py-2.5 rounded-xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-between gap-3">
          <div>
            <div className="text-[10px] font-semibold text-white/55">Waiting for open positions to close</div>
            <div className="text-[9px] text-white/25 mt-0.5">New entries stopped. TP orders remain until filled. Use hard stop to exit immediately.</div>
          </div>
          <span className="flex-shrink-0 w-3 h-3">
            <svg className="animate-spin w-3 h-3" viewBox="0 0 12 12" fill="none">
              <circle cx="6" cy="6" r="4.5" stroke="rgba(255,255,255,0.1)" strokeWidth="1.5"/>
              <path d="M6 1.5a4.5 4.5 0 0 1 4.5 4.5" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </span>
        </div>
      )}

      {/* -- Capital + Net PnL hero ------------------------------------------- */}
      <div className="px-5 py-4 flex items-end justify-between border-b border-white/[0.03]">
        <div>
          <div className="text-[9px] text-white/20 uppercase tracking-widest font-semibold mb-1">Capital</div>
          <div className="text-[20px] font-bold font-mono tabular-nums leading-none text-white/85">
            {fmtUsd(session.gross_capital)}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[9px] text-white/20 uppercase tracking-widest font-semibold mb-1">Net PnL</div>
          <div className={`text-[20px] font-bold font-mono tabular-nums leading-none ${loaded ? pnlColor(netPnl) : 'text-white/15'}`}>
            {loaded ? fmtUsd(netPnl) : <Sk w="w-20" h="h-5" />}
          </div>
        </div>
      </div>

      {/* -- Summary stats --------------------------------------------------- */}
      <div className="px-5 py-3 grid grid-cols-5 gap-3 border-b border-white/[0.03]">
        {loaded ? (
          <>
            {(() => {
              const vol = rt!.total_volume
              const size = session.gross_capital
              const uptime = rt!.uptime_s ?? 0
              const totalX = size > 0 ? vol / size : null
              const dailyX = totalX != null && uptime > 0 ? totalX / (uptime / 86400) : null
              const fmtX = (x: number) => `${x.toFixed(2)}×`
              return (
                <MiniStat
                  label="Volume"
                  value={fmtUsd(vol, 2)}
                  sub={totalX != null ? (
                    <>
                      <span className="hidden sm:inline">{fmtX(totalX)} total · {dailyX != null ? fmtX(dailyX) : '·'} / 24h</span>
                      <span className="sm:hidden flex flex-col gap-0">{fmtX(totalX)} total<br />{dailyX != null ? fmtX(dailyX) : '·'} 24h</span>
                    </>
                  ) : undefined}
                />
              )
            })()}
            <MiniStat label="Orders"   value={String(rt!.order_count ?? rt!.trade_count)} />
            <MiniStat label="Realized" value={fmtUsd(realizedPnl, 2)} color={pnlColor(realizedPnl)} />
            <MiniStat label="Fees"     value={fmtUsdFloor(fees)} color="text-white/25" />
            {(() => {
              const vol = rt!.total_volume
              const cpm = vol > 0 ? (netPnl / vol) * 1_000_000 : null
              const sign = cpm != null && cpm > 0 ? '+' : ''
              return (
                <MiniStat
                  label="CPM"
                  value={cpm != null ? `${sign}${fmtUsd(cpm, 0)}` : '$'}
                  color={cpm != null ? pnlColor(cpm) : 'text-white/25'}
                  tooltipTitle="Cost per Million"
                  tooltip="Net PnL extrapolated to $1M of volume."
                />
              )
            })()}
          </>
        ) : (
          <>
            {(['Volume','Orders','Realized','Fees','CPM'] as const).map(l => (
              <div key={l}>
                <div className="text-[9px] text-white/20 uppercase tracking-widest font-semibold mb-1">{l}</div>
                <Sk w="w-12" h="h-3" />
              </div>
            ))}
          </>
        )}
      </div>

      {/* -- Progress -------------------------------------------------------- */}
      <div className="px-5 py-3 border-b border-white/[0.03]">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[9px] text-white/20 uppercase tracking-widest font-semibold">
            {session.stop_mode === 'days' ? 'Duration' : 'Volume target'}
          </span>
          <span className="text-[9px] font-mono text-white/25">
            {progressLabel ?? <Sk w="w-16" h="h-2.5" />}
          </span>
        </div>
        <div className="h-[3px] bg-white/[0.05] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-1000"
            style={{
              width: progressPct != null ? `${progressPct}%` : '0%',
              background: (progressPct ?? 0) >= 95 ? '#4ade80' : 'rgb(99 102 241)',
            }}
          />
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-[9px] font-mono text-white/15">SL {session.session_sl_pct}%</span>
          <span className="text-[9px] font-mono text-white/18">
            {progressPct != null ? `${progressPct.toFixed(1)}%` : ''}
          </span>
        </div>
      </div>

      {/* -- Slots (positions + orders, collapsed) --------------------------- */}
      <SlotActivityList slots={slotStats} loaded={loaded} />

      {/* -- Stop modal ------------------------------------------------------ */}
      {showStopModal && (
        <StopModal
          stopping={stopping}
          isStopping={isStopping}
          onStop={stop}
          onClose={() => setShowStopModal(false)}
        />
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Launch form
// -----------------------------------------------------------------------------

function LaunchForm({ strategy, walletId, usdcBalance, onLaunched, displayName }: {
  strategy: Strategy; walletId: string; usdcBalance: number | null; onLaunched: () => void; displayName?: string
}) {
  const addToast = useAppStore(s => s.addToast)
  const [capital,    setCapital]    = useState('')
  const [stopMode,   setStopMode]   = useState<'days' | 'volume'>('days')
  const [stopDays,   setStopDays]   = useState('7')
  const [targetVol,  setTargetVol]  = useState('50000')
  const [slPct,      setSlPct]      = useState('20')
  const [thresholds, setThresholds] = useState<{ min: number; rec: number } | null>(null)
  const [launching,  setLaunching]  = useState(false)
  const [launchError, setLaunchError] = useState<string | null>(null)

  const gross      = parseFloat(capital) || 0
  const reservePct = strategy.reserve_pct
  const deployable = gross * (1 - reservePct / 100)
  const dailyVol   = deployable * 12

  const daysVal      = parseFloat(stopDays) || 0
  const targetVolVal = parseFloat(targetVol) || 0

  // Duration-mode estimates
  const estVolume  = dailyVol * daysVal
  const pnlHigh    = deployable * 0.0025 * daysVal
  const pnlLow     = -(deployable * 0.005 * daysVal)

  // Volume-mode estimates
  const estHours   = dailyVol > 0 ? (targetVolVal / dailyVol) * 24 : 0
  const estDays    = estHours / 24
  const pnlHighVol = deployable * 0.0025 * estDays
  const pnlLowVol  = -(deployable * 0.005 * estDays)

  const overBalance = usdcBalance != null && gross > usdcBalance
  const belowMin    = thresholds != null && gross > 0 && gross < thresholds.min
  const belowRec    = thresholds != null && gross > 0 && gross >= thresholds.min && gross < thresholds.rec
  const canLaunch   = gross > 0 && !overBalance && !belowMin && !launching

  type VS = 'idle' | 'ok' | 'warn' | 'err'
  const vs: VS = !gross ? 'idle' : overBalance || belowMin ? 'err' : belowRec ? 'warn' : 'ok'

  const vCfg = {
    idle: { border: 'border-white/[0.06]',    bg: '',                         dot: '',               label: '' },
    ok:   { border: 'border-emerald-500/20',   bg: 'bg-emerald-500/[0.04]',    dot: 'text-emerald-400', label: 'Ready to launch' },
    warn: { border: 'border-amber-400/20',     bg: 'bg-amber-400/[0.04]',      dot: 'text-amber-400',   label: 'Below suggested' },
    err:  { border: 'border-red-500/20',       bg: 'bg-red-500/[0.04]',        dot: 'text-red-400',     label: overBalance ? 'Exceeds balance' : 'Below minimum' },
  }[vs]

  useEffect(() => {
    api.checkViability(999_999)
      .then((r: any) => setThresholds({ min: r.min_capital, rec: r.recommended_capital }))
      .catch(() => {})
  }, [])

  async function launch() {
    if (!canLaunch) return
    setLaunching(true)
    setLaunchError(null)
    try {
      await api.launchSession({
        wallet_id:      walletId,
        gross_capital:  gross,
        stop_mode:      stopMode,
        stop_days:      stopMode === 'days' ? parseInt(stopDays) : null,
        target_volume:  stopMode === 'volume' ? parseFloat(targetVol) : null,
        session_sl_pct: parseFloat(slPct),
      })
      addToast({ type: 'success', message: 'Session launched' })
      onLaunched()
    } catch (e: any) {
      setLaunchError(e.message ?? 'Launch failed')
      setLaunching(false)
    }
  }

  const vMsg =
    vs === 'err'  ? (overBalance ? 'Exceeds balance' : 'Below minimum') :
    vs === 'warn' ? 'Below suggested' : ''

  const vMsgColor = vs === 'err' ? 'text-red-400/70' : 'text-amber-400/65'

  // Show unified loader immediately on click; keep showing it (with error state) if API fails
  if (launching || launchError) {
    return (
      <SessionStartLoader
        phase="api"
        error={launchError}
        displayName={displayName}
        onRetry={() => { setLaunchError(null); setLaunching(false) }}
      />
    )
  }

  return (
    <div className="flex flex-col gap-0">

      {/* -- Header: balance ------------------------------------------------ */}
      <div className="px-5 pt-5 pb-4 border-b border-white/[0.05]">
        <Label>Trading Balance</Label>
        <div className="mt-1 text-[22px] font-bold font-mono tabular-nums leading-none text-white/85">
          {usdcBalance != null
            ? `$${usdcBalance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : <span className="text-white/20 text-lg">...</span>}
        </div>
      </div>

      {/* -- Form grid ------------------------------------------------------ */}
      <div className="p-5 grid grid-cols-2 gap-x-5 gap-y-4 items-start">

        {/* Capital */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <Label>Capital</Label>
            {usdcBalance != null && (
              <div className="flex gap-1">
                {([['25%', 0.25], ['50%', 0.5], ['MAX', 1]] as [string, number][]).map(([lbl, f]) => (
                  <button
                    key={lbl}
                    className="text-[9px] px-1.5 py-0.5 border border-white/[0.08] rounded
                               text-white/22 font-semibold hover:border-brand/40 hover:text-brand/65 transition-all"
                    onClick={() => setCapital(String(Math.floor((usdcBalance ?? 0) * f * 100) / 100))}
                  >{lbl}</button>
                ))}
              </div>
            )}
          </div>
          <Field
            value={capital} onChange={setCapital}
            placeholder="0.00" suffix="USDC"
            error={overBalance || belowMin}
          />
          {/* Min / suggested + viability on same line */}
          <div className="flex items-center justify-between h-4">
            <span className="text-[10px] text-white/22">
              {thresholds
                ? <>Min <span className="font-mono text-white/35">{fmtUsd(thresholds.min)}</span><span className="text-white/15"> · {fmtUsd(thresholds.rec)} suggested</span></>
                : <span className="text-white/15">...</span>}
            </span>
            {vMsg && (
              <span className={`text-[10px] font-medium ${vMsgColor}`}>{vMsg}</span>
            )}
          </div>
        </div>

        {/* Stop when */}
        <div className="flex flex-col gap-2">
          <Label>Stop when</Label>
          <div className="grid grid-cols-2 gap-0.5 p-0.5 bg-white/[0.03] rounded-xl">
            {(['days', 'volume'] as const).map(m => (
              <button key={m} onClick={() => setStopMode(m)}
                className={`py-2 rounded-[10px] text-[11px] font-semibold transition-all
                  ${stopMode === m ? 'bg-brand/85 text-white' : 'text-white/25 hover:text-white/45'}`}
              >
                {m === 'days' ? 'Duration' : 'Volume'}
              </button>
            ))}
          </div>
          {stopMode === 'days'
            ? <Field value={stopDays}  onChange={setStopDays}  placeholder="30"    suffix="days" integer />
            : <Field value={targetVol} onChange={setTargetVol} placeholder="50000" suffix="USDC" />
          }
          <div className="h-4" />{/* align with capital's threshold row */}
        </div>

        {/* Stop-loss */}
        <div className="flex flex-col gap-2">
          <Label>Stop-loss</Label>
          <Field
            value={slPct}
            onChange={v => { if (v === '' || parseFloat(v) <= 100) setSlPct(v) }}
            placeholder="20"
            suffix="% capital"
          />
        </div>

        {/* Estimates */}
        <div className="bg-white/[0.02] border border-white/[0.05] rounded-xl px-3.5 py-2.5 space-y-1.5">
          <div className="flex items-baseline gap-2">
            <span className="text-[10px] text-white/28 shrink-0">
              {stopMode === 'days' ? 'Est. volume' : 'Est. duration'}
            </span>
            <span className="text-[11px] font-mono text-white/55 tabular-nums">
              {stopMode === 'days' ? fmtUsd(estVolume) : fmtDur(estHours)}
            </span>
          </div>
          {(() => {
            const hi  = stopMode === 'days' ? pnlHigh    : pnlHighVol
            const lo  = stopMode === 'days' ? pnlLow     : pnlLowVol
            const vol = stopMode === 'days' ? estVolume  : targetVolVal
            const mid = (hi + lo) / 2
            const costPerM = vol > 0 ? Math.abs(mid) / vol * 1_000_000 : 0
            return (
              <div className="flex items-baseline gap-2">
                <span className="text-[10px] text-white/28 shrink-0">PnL range</span>
                <span className="text-[11px] font-mono tabular-nums">
                  <span className="text-emerald-400/60">+{fmtUsd(hi)}</span>
                  <span className="text-white/15"> / </span>
                  <span className="text-red-400/55">{fmtUsd(lo)}</span>
                </span>
                {costPerM > 0 && (
                  <span className="text-[10px] text-white/18 font-mono tabular-nums">
                    ({fmtUsd(costPerM)}/M)
                  </span>
                )}
              </div>
            )
          })()}
          <div className="text-[10px] text-white/22 pt-1 border-t border-white/[0.04]">
            Based on previous sessions data: ~12x volume daily · +0.25%-0.5%/day
          </div>
        </div>

      </div>

      {/* Launch */}
      <div className="px-5 pb-5">
        <button
          className="w-full py-3.5 rounded-xl text-sm font-bold transition-all
            bg-brand text-white hover:opacity-90 active:scale-[0.99]
            disabled:opacity-20 disabled:cursor-not-allowed disabled:active:scale-100"
          disabled={!canLaunch}
          onClick={launch}
        >
          {launching ? 'Launching...' : 'Launch Session'}
        </button>
      </div>

    </div>
  )
}

// -----------------------------------------------------------------------------
// RunTab
// -----------------------------------------------------------------------------

export function RunTab({ user, strategy, activeSession, loading, usdcBalance, onSessionChange }: Props) {
  const { setActiveSession } = useAppStore()
  const rtStatus = activeSession?.runtime?.status
  const rtTerminal = rtStatus && ['stopped', 'error'].includes(rtStatus)
  const dbTerminal = activeSession && ['stopped', 'error'].includes(activeSession.status)
  // Latch: once we have an active session, hold hasActive=true even through
  // brief null/undefined activeSession gaps (WS reconnect, render races).
  // Only release when explicitly stopped (onStopped) or terminal status confirmed.
  const hasActiveRef = useRef(false)
  if (activeSession && !dbTerminal && !rtTerminal) hasActiveRef.current = true
  if (!activeSession && !hasActiveRef.current) { /* never latched  stay false */ }
  if (dbTerminal || rtTerminal) hasActiveRef.current = false
  const hasActive = hasActiveRef.current
  const walletId  = user.wallet_id || ''

  if (loading) {
    return (
      <div className="p-5 space-y-3">
        {[64, 44, 88].map(h => (
          <div key={h} className="bg-white/[0.03] rounded-xl animate-pulse" style={{ height: h }} />
        ))}
      </div>
    )
  }

  if (!strategy) {
    return (
      <div className="p-5">
        <div className="border border-white/[0.06] rounded-xl p-10 text-center">
          <div className="text-white/20 text-xs font-mono uppercase tracking-widest">No strategy configured</div>
          <div className="text-white/15 text-[10px] mt-1.5">Platform is being set up. Check back soon.</div>
        </div>
      </div>
    )
  }

  if (!strategy.available) {
    return (
      <div className="p-5">
        <div className="border border-orange-500/15 rounded-xl px-4 py-3.5 bg-orange-500/[0.04]">
          <div className="text-[10px] text-orange-400/55 uppercase tracking-widest font-semibold mb-1">Unavailable</div>
          <div className="text-xs text-orange-400/40 leading-relaxed">{strategy.unavailable_reason}</div>
        </div>
      </div>
    )
  }

  const displayName = user.display_name || user.wallet_address?.slice(0, 6) || undefined

  return hasActive ? (
    <ActiveSession
      session={activeSession!}
      displayName={displayName}
      onStopped={() => { hasActiveRef.current = false; onSessionChange(null); setActiveSession(null) }}
    />
  ) : (
    <LaunchForm
      strategy={strategy}
      walletId={walletId}
      displayName={displayName}
      usdcBalance={usdcBalance}
      onLaunched={() => {
        api.getActiveSession()
          .then((s: any) => { onSessionChange(s); setActiveSession(s) })
          .catch(() => {})
      }}
    />
  )
}
