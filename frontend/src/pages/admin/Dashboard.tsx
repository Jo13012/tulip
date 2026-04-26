import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from '../../utils/api'
import { fmtUsd, pnlColor } from '../../utils/format'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Metrics {
  total_users:              number
  active_sessions:          number
  stopping_sessions:        number
  recovering_sessions:      number
  degraded_sessions:        number
  total_sessions:           number
  total_gross_capital:      number
  total_deployable_capital: number
  total_deployed_capital:   number
  total_trading_balance:    number
  live_total_volume:        number
  live_total_pnl:           number
  live_total_fees:          number
}

interface VersionPerf {
  version_id:    string
  version_label: string
  published_at:  string | null
  session_count: number
  total_volume:  number
  total_pnl:     number
  total_fees:    number
}

interface MarketPerf {
  polymarket_url: string
  question:       string
  volume:         number
  realized_pnl:   number
  fees_paid:      number
  exposure_count: number
}

interface StrategyData {
  total_sessions:  number
  active_sessions: number
  total_volume:    number
  total_pnl:       number
  total_fees:      number
  volume_multiple: number
  versions:        VersionPerf[]
  markets:         MarketPerf[]
}

interface DayVolume    { date: string; volume: number; fees?: number }
interface DayCpm      { date: string; volume: number; gross_cpm: number | null; net_cpm: number | null }
interface SessionMultiple { session_id: string; date: string; volume: number; run_days: number; capital: number; gross_multiple: number; net_multiple: number; gross_pnl: number; fees: number }

type SubTab = 'overview' | 'versions' | 'analytics' | 'referrals'

interface ReferredUser {
  user_id: string
  display_name: string
  volume: number
  referrer_earned: number
}

interface ReferrerRow {
  referrer_id: string
  display_name: string
  referral_count: number
  referred_volume: number
  referrer_earnings: number
  referrals: ReferredUser[]
}

// ── Shared UI ─────────────────────────────────────────────────────────────────

function Stat({ label, value, accent = 'text-white/65', sub }: {
  label: string; value: React.ReactNode; accent?: string; sub?: string
}) {
  return (
    <div className="bg-[#0d0e10] border border-white/[0.05] rounded-xl px-4 py-3">
      <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">{label}</div>
      <div className={`text-[17px] font-bold font-mono tabular-nums leading-none ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-white/20 mt-1 font-mono">{sub}</div>}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-2">{title}</div>
      {children}
    </div>
  )
}

function PulseDot({ color }: { color: string }) {
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${color} mr-1.5 animate-pulse`} />
}

// ── Bar chart ─────────────────────────────────────────────────────────────────

const SCALE_TICKS = [0, 1_000, 5_000, 10_000, 20_000, 50_000, 100_000]

function fmtVolLabel(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(0)}k`
  return `$${v}`
}

function fmtShortDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

const CHART_PAD_L = 52
const CHART_PAD_R = 12
const CHART_PAD_T = 16
const CHART_PAD_B = 28
const CHART_W = 900
const CHART_H = 180

function buildIndexBands(length: number, chartW: number) {
  if (length <= 0) return []
  return Array.from({ length }, (_, i) => {
    if (length === 1) return { left: 0, width: chartW, center: chartW / 2 }
    const step = chartW / (length - 1)
    const center = i * step
    const left = i === 0 ? 0 : center - step / 2
    const right = i === length - 1 ? chartW : center + step / 2
    return { left, width: right - left, center }
  })
}

function groupMultipleByDate(rows: SessionMultiple[]): SessionMultiple[] {
  const grouped = new Map<string, {
    date: string
    volume: number
    gross_pnl: number
    fees: number
    capital: number
    capital_days: number
    run_days_weighted: number
    count: number
  }>()

  for (const row of rows) {
    const capitalDays = Math.max(0, row.capital) * Math.max(0, row.run_days)
    const current = grouped.get(row.date) ?? {
      date: row.date,
      volume: 0,
      gross_pnl: 0,
      fees: 0,
      capital: 0,
      capital_days: 0,
      run_days_weighted: 0,
      count: 0,
    }
    current.volume += row.volume || 0
    current.gross_pnl += row.gross_pnl || 0
    current.fees += row.fees || 0
    current.capital += row.capital || 0
    current.capital_days += capitalDays
    current.run_days_weighted += capitalDays
    current.count += 1
    grouped.set(row.date, current)
  }

  return Array.from(grouped.values())
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((row) => {
      const gross_multiple = row.capital_days > 0 ? row.volume / row.capital_days : 0
      const net_multiple = row.capital_days > 0 ? (row.volume - row.fees) / row.capital_days : 0
      const avg_run_days = row.capital > 0 ? row.run_days_weighted / row.capital : 0
      return {
        session_id: `group:${row.date}`,
        date: row.date,
        volume: Number(row.volume.toFixed(2)),
        run_days: Number(avg_run_days.toFixed(3)),
        capital: Number(row.capital.toFixed(2)),
        gross_multiple: Number(gross_multiple.toFixed(4)),
        net_multiple: Number(net_multiple.toFixed(4)),
        gross_pnl: Number(row.gross_pnl.toFixed(2)),
        fees: Number(row.fees.toFixed(2)),
      }
    })
}

function VolumeChart({ data }: { data: DayVolume[] }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; date: string; volume: number; fees: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-40 text-white/15 text-xs font-mono uppercase tracking-widest">
        No data yet
      </div>
    )
  }

  const maxVol = Math.max(...data.map(d => d.volume))
  const ceilTick = SCALE_TICKS.find(t => t >= maxVol) ?? Math.ceil(maxVol / 10_000) * 10_000

  const PAD_L = 52
  const PAD_R = 12
  const PAD_T = 16
  const PAD_B = 28
  const W     = 900
  const H     = 180

  const chartW = W - PAD_L - PAD_R
  const chartH = H - PAD_T - PAD_B

  const barW = Math.max(4, chartW / data.length - 2)
  const gap  = chartW / data.length

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(p => ({
    pct: p, val: ceilTick * p, y: PAD_T + chartH * (1 - p),
  }))

  const step    = Math.max(1, Math.floor(data.length / 10))
  const xLabels = data.filter((_, i) => i % step === 0 || i === data.length - 1)

  return (
    <div className="relative select-none">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
        onMouseLeave={() => setTooltip(null)}
      >
        {yTicks.map(t => (
          <g key={t.pct}>
            <line x1={PAD_L} y1={t.y} x2={W - PAD_R} y2={t.y} stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
            <text x={PAD_L - 6} y={t.y + 3.5} textAnchor="end" className="fill-white/20" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {fmtVolLabel(t.val)}
            </text>
          </g>
        ))}

        {data.map((d, i) => {
          const pct  = ceilTick > 0 ? d.volume / ceilTick : 0
          const barH = Math.max(pct > 0 ? 2 : 0, chartH * pct)
          const x    = PAD_L + i * gap + (gap - barW) / 2
          const y    = PAD_T + chartH - barH
          const isHov = tooltip?.date === d.date
          return (
            <rect
              key={d.date}
              x={x} y={y} width={barW} height={barH } rx={2}
              fill={isHov ? 'rgba(16,185,129,0.75)' : 'rgba(16,185,129,0.40)'}
              className="cursor-default transition-colors"
              onMouseEnter={(e) => {
                const r = svgRef.current?.getBoundingClientRect()
                if (!r) return
                setTooltip({ x: e.clientX - r.left, y: e.clientY - r.top, date: d.date, volume: d.volume, fees: d.fees ?? 0 })
              }}
            />
          )
        })}

        {xLabels.map(d => {
          const i = data.indexOf(d)
          const x = PAD_L + i * gap + gap / 2
          return (
            <text key={d.date} x={x} y={H - 4} textAnchor="middle" className="fill-white/20" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {fmtShortDate(d.date)}
            </text>
          )
        })}
      </svg>

      {tooltip && (
        <div
          className="pointer-events-none absolute z-50 bg-[#1e1f23] border border-white/[0.10] rounded-lg shadow-xl px-3 py-2"
          style={{
            left: Math.min(tooltip.x + 12, (svgRef.current?.clientWidth ?? 300) - 120),
            top: Math.max(8, tooltip.y - 40),
            transform: 'translateY(-50%)',
          }}
        >
          <div className="text-[10px] font-semibold text-white/70 mb-0.5">{fmtShortDate(tooltip.date)}</div>
          <div className="text-[12px] font-mono font-bold text-emerald-400">{fmtUsd(tooltip.volume, 0)}</div>
          {tooltip.fees > 0 && (
            <div className="text-[10px] font-mono text-amber-400/70">fees {fmtUsd(tooltip.fees, 2)}</div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Catmull-Rom spline ────────────────────────────────────────────────────────
// Remplace les segments droits par des courbes lisses

function catmullRom(points: { x: number; y: number }[], tension = 0.4): string {
  if (points.length < 2) return ""
  const pts = [points[0], ...points, points[points.length - 1]]
  let d = `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`
  for (let i = 1; i < points.length; i++) {
    const p0 = pts[i - 1], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2]
    const cp1x = p1.x + (p2.x - p0.x) * tension
    const cp1y = p1.y + (p2.y - p0.y) * tension
    const cp2x = p2.x - (p3.x - p1.x) * tension
    const cp2y = p2.y - (p3.y - p1.y) * tension
    d += ` C${cp1x.toFixed(2)},${cp1y.toFixed(2)} ${cp2x.toFixed(2)},${cp2y.toFixed(2)} ${p2.x.toFixed(2)},${p2.y.toFixed(2)}`
  }
  return d
}

// ── Animated line (draw-on effect) ────────────────────────────────────────────

function AnimatedLinePath({ d }: { d: string }) {
  const ref = useRef<SVGPathElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const len = el.getTotalLength?.() ?? 600
    el.style.strokeDasharray = String(len)
    el.style.strokeDashoffset = String(len)
    el.style.transition = "none"
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.style.transition = "stroke-dashoffset 1.1s cubic-bezier(0.4,0,0.2,1)"
        el.style.strokeDashoffset = "0"
      })
    })
  }, [d])
  return (
    <>
      {/* Glow layer derrière */}
      <path d={d} stroke="rgba(52,211,153,0.22)" strokeWidth={4} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      {/* Ligne principale animée */}
      <path ref={ref} d={d} stroke="rgba(52,211,153,0.92)" strokeWidth={1.6} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </>
  )
}

// ── Glow dot ──────────────────────────────────────────────────────────────────

function GlowDot({ cx, cy, active }: { cx: number; cy: number; active: boolean }) {
  return (
    <g>
      {active && (
        <>
          <circle cx={cx} cy={cy} r={14} fill="rgba(52,211,153,1)" opacity={0.07} />
          <circle cx={cx} cy={cy} r={8}  fill="rgba(52,211,153,1)" opacity={0.13} />
        </>
      )}
      <circle
        cx={cx} cy={cy}
        r={active ? 4.5 : 3}
        fill="rgba(52,211,153,0.96)"
        stroke={active ? "rgba(255,255,255,0.5)" : "rgba(255,255,255,0.1)"}
        strokeWidth={active ? 1.5 : 1}
        style={{ transition: "r 0.12s, opacity 0.12s" }}
      />
    </g>
  )
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
// Remplace ton TooltipShell — se repositionne automatiquement aux bords

function TooltipShell({
  x, y, children, svgRef, width = 120,
}: {
  x: number; y: number; children: React.ReactNode
  svgRef: React.RefObject<SVGSVGElement>; width?: number
}) {
  const HEIGHT_EST = 72
  const [pos, setPos] = useState({ tx: 0, ty: 0 })

  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const svgW = el.clientWidth
    const svgH = el.clientHeight
    let tx = x + 14
    let ty = y - HEIGHT_EST / 2
    if (tx + width > svgW - 8) tx = x - width - 14
    if (ty < 8) ty = 8
    if (ty + HEIGHT_EST > svgH - 8) ty = svgH - HEIGHT_EST - 8
    setPos({ tx, ty })
  }, [x, y, svgRef, width])

  return (
    <div
      style={{
        position: "absolute",
        left: pos.tx,
        top: pos.ty,
        width,
        pointerEvents: "none",
        zIndex: 20,
      }}
    >
      <div style={{
        background: "rgba(10,12,18,0.96)",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 10,
        padding: "9px 12px",
        backdropFilter: "blur(14px)",
        boxShadow: "0 8px 32px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.05)",
      }}>
        {children}
      </div>
    </div>
  )
}

// ── CPM Line Chart ─────────────────────────────────────────────────────────────

function CpmChart({ data, mode }: { data: DayCpm[]; mode: 'gross' | 'net' }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; d: DayCpm; idx: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const pts = data.filter(d => (mode === 'gross' ? d.gross_cpm : d.net_cpm) !== null) as DayCpm[]

  if (!pts.length) {
    return <div className="flex items-center justify-center h-40 text-white/15 text-xs font-mono uppercase tracking-widest">No data yet</div>
  }

  const vals = pts.map(d => (mode === 'gross' ? d.gross_cpm! : d.net_cpm!))
  const minV = Math.min(...vals)
  const maxV = Math.max(...vals)
  const rangeV = maxV - minV || 1
  const chartW = CHART_W - CHART_PAD_L - CHART_PAD_R
  const chartH = CHART_H - CHART_PAD_T - CHART_PAD_B
  const bands = buildIndexBands(pts.length, chartW)

  const toX = (i: number) => CHART_PAD_L + bands[i].center
  const toY = (v: number) => CHART_PAD_T + chartH - ((v - minV) / rangeV) * chartH

  const avg = vals.reduce((sum, v) => sum + v, 0) / vals.length
  const avgY = toY(avg)

  const yTicks = Array.from({ length: 5 }, (_, i) => {
    const v = minV + (rangeV * i) / 4
    return { v, y: toY(v) }
  })

  const step = Math.max(1, Math.floor(pts.length / 8))
  const xLabels = pts.filter((_, i) => i % step === 0 || i === pts.length - 1)

  const splinePts = pts.map((d, i) => ({
    x: toX(i),
    y: toY(mode === 'gross' ? d.gross_cpm! : d.net_cpm!),
  }))
  const linePath = catmullRom(splinePts)
  const areaPath = linePath
    + ` L${toX(pts.length - 1).toFixed(2)},${CHART_PAD_T + chartH}`
    + ` L${toX(0).toFixed(2)},${CHART_PAD_T + chartH} Z`

  const fillId = `cpm-area-${mode}`

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const scale = CHART_W / (svgRef.current?.clientWidth ?? CHART_W)
    const localX = Math.min(Math.max((e.clientX - rect.left) * scale - CHART_PAD_L, 0), chartW)
    const idx = bands.reduce((best, band, i) =>
      Math.abs(localX - band.center) < Math.abs(localX - bands[best].center) ? i : best, 0)
    setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, d: pts[idx], idx })
  }

  return (
    <div className="relative select-none">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
        onMouseMove={handleMove}
        onMouseLeave={() => setTooltip(null)}
      >
        <defs>
          <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="rgba(52,211,153,0.18)" />
            <stop offset="60%"  stopColor="rgba(52,211,153,0.04)" />
            <stop offset="100%" stopColor="rgba(52,211,153,0)" />
          </linearGradient>
        </defs>

        {/* Grid */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={CHART_PAD_L} y1={t.y} x2={CHART_W - CHART_PAD_R} y2={t.y} stroke="rgba(255,255,255,0.035)" strokeWidth="1" />
            <text x={CHART_PAD_L - 8} y={t.y + 3.5} textAnchor="end" className="fill-white/20" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {Math.round(t.v)}
            </text>
          </g>
        ))}

        {/* Hover band + crosshair */}
        {tooltip && (
          <>
            <rect
              x={CHART_PAD_L + bands[tooltip.idx].left}
              y={CHART_PAD_T}
              width={bands[tooltip.idx].width}
              height={chartH}
              rx={4}
              fill="rgba(255,255,255,0.025)"
            />
            <line
              x1={toX(tooltip.idx)} y1={CHART_PAD_T}
              x2={toX(tooltip.idx)} y2={CHART_PAD_T + chartH}
              stroke="rgba(255,255,255,0.1)"
              strokeWidth="1"
              strokeDasharray="4,5"
            />
          </>
        )}

        {/* Avg line */}
        <line x1={CHART_PAD_L - 6} y1={avgY} x2={CHART_PAD_L} y2={avgY} stroke="rgba(251,191,36,0.7)" strokeWidth="1.5" />
        <text x={CHART_PAD_L - 8} y={avgY + 3.5} textAnchor="end" className="fill-yellow-400/65" style={{ fontSize: 9, fontFamily: 'monospace' }}>
          {Math.round(avg)}
        </text>
        <line x1={CHART_PAD_L} y1={avgY} x2={CHART_W - CHART_PAD_R} y2={avgY} stroke="rgba(251,191,36,0.4)" strokeWidth="1" strokeDasharray="5,5" />
        <text x={CHART_W - CHART_PAD_R - 4} y={avgY - 4} textAnchor="end" className="fill-yellow-400/45" style={{ fontSize: 8, fontFamily: 'monospace' }}>avg</text>

        {/* Area fill + smooth animated line */}
        <path d={areaPath} fill={`url(#${fillId})`} />
        <AnimatedLinePath d={linePath} />

        {/* Dots */}
        {pts.map((d, i) => {
          const v = mode === 'gross' ? d.gross_cpm! : d.net_cpm!
          return (
            <g key={d.date}>
              <rect x={CHART_PAD_L + bands[i].left} y={CHART_PAD_T} width={bands[i].width} height={chartH} fill="transparent" />
              <GlowDot cx={toX(i)} cy={toY(v)} active={tooltip?.idx === i} />
            </g>
          )
        })}

        {/* X labels */}
        {xLabels.map(d => {
          const i = pts.indexOf(d)
          return (
            <text key={d.date} x={toX(i)} y={CHART_H - 4} textAnchor="middle" className="fill-white/20" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {fmtShortDate(d.date)}
            </text>
          )
        })}
      </svg>

      {tooltip && (
        <TooltipShell x={tooltip.x} y={tooltip.y} svgRef={svgRef} width={148}>
          <div className="text-[10px] font-mono text-white/40 mb-1">{fmtShortDate(tooltip.d.date)}</div>
          <div className="font-mono font-bold text-emerald-400" style={{ fontSize: 20, lineHeight: 1.1 }}>
            {Math.round(mode === 'gross' ? tooltip.d.gross_cpm! : tooltip.d.net_cpm!)}
            <span className="text-[11px] font-medium text-emerald-400/60 ml-1">CPM</span>
          </div>
          </TooltipShell>
      )}
    </div>
  )
}

// ── Multiple Scatter/Line Chart ────────────────────────────────────────────────

function MultipleChart({ data, mode }: { data: SessionMultiple[]; mode: 'gross' | 'net' }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; d: SessionMultiple; idx: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  if (!data.length) {
    return <div className="flex items-center justify-center h-40 text-white/15 text-xs font-mono uppercase tracking-widest">No data yet</div>
  }

  const vals = data.map(d => mode === 'gross' ? d.gross_multiple : d.net_multiple)
  const maxV = Math.max(...vals, 0.1)
  const avg = vals.reduce((sum, v) => sum + v, 0) / vals.length
  const chartW = CHART_W - CHART_PAD_L - CHART_PAD_R
  const chartH = CHART_H - CHART_PAD_T - CHART_PAD_B
  const bands = buildIndexBands(data.length, chartW)

  const toX = (i: number) => CHART_PAD_L + bands[i].center
  const toY = (v: number) => CHART_PAD_T + chartH - (v / maxV) * chartH
  const avgY = toY(avg)

  const yTicks = Array.from({ length: 5 }, (_, i) => {
    const v = maxV * i / 4
    return { v, y: toY(v) }
  })

  const step = data.length <= 12 ? 1 : Math.max(1, Math.floor(data.length / 8))
  const xLabels = data.filter((_, i) => i % step === 0 || i === data.length - 1)

  const splinePts = data.map((d, i) => ({
    x: toX(i),
    y: toY(mode === 'gross' ? d.gross_multiple : d.net_multiple),
  }))
  const linePath = catmullRom(splinePts)
  const areaPath = linePath
    + ` L${toX(data.length - 1).toFixed(2)},${CHART_PAD_T + chartH}`
    + ` L${toX(0).toFixed(2)},${CHART_PAD_T + chartH} Z`

  const fillId = `multiple-area-${mode}`

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const scale = CHART_W / (svgRef.current?.clientWidth ?? CHART_W)
    const localX = Math.min(Math.max((e.clientX - rect.left) * scale - CHART_PAD_L, 0), chartW)
    const idx = bands.reduce((best, band, i) =>
      Math.abs(localX - band.center) < Math.abs(localX - bands[best].center) ? i : best, 0)
    setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, d: data[idx], idx })
  }

  return (
    <div className="relative select-none">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
        onMouseMove={handleMove}
        onMouseLeave={() => setTooltip(null)}
      >
        <defs>
          <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="rgba(52,211,153,0.18)" />
            <stop offset="60%"  stopColor="rgba(52,211,153,0.04)" />
            <stop offset="100%" stopColor="rgba(52,211,153,0)" />
          </linearGradient>
        </defs>

        {/* Grid */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={CHART_PAD_L} y1={t.y} x2={CHART_W - CHART_PAD_R} y2={t.y} stroke="rgba(255,255,255,0.035)" strokeWidth="1" />
            <text x={CHART_PAD_L - 8} y={t.y + 3.5} textAnchor="end" className="fill-white/20" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {t.v.toFixed(1)}x
            </text>
          </g>
        ))}

        {/* Hover band + crosshair */}
        {tooltip && (
          <>
            <rect
              x={CHART_PAD_L + bands[tooltip.idx].left}
              y={CHART_PAD_T}
              width={bands[tooltip.idx].width}
              height={chartH}
              rx={4}
              fill="rgba(255,255,255,0.025)"
            />
            <line
              x1={toX(tooltip.idx)} y1={CHART_PAD_T}
              x2={toX(tooltip.idx)} y2={CHART_PAD_T + chartH}
              stroke="rgba(255,255,255,0.1)"
              strokeWidth="1"
              strokeDasharray="4,5"
            />
          </>
        )}

        {/* Avg line */}
        <line x1={CHART_PAD_L - 6} y1={avgY} x2={CHART_PAD_L} y2={avgY} stroke="rgba(251,191,36,0.7)" strokeWidth="1.5" />
        <text x={CHART_PAD_L - 8} y={avgY + 3.5} textAnchor="end" className="fill-yellow-400/65" style={{ fontSize: 9, fontFamily: 'monospace' }}>
          {avg.toFixed(1)}x
        </text>
        <line x1={CHART_PAD_L} y1={avgY} x2={CHART_W - CHART_PAD_R} y2={avgY} stroke="rgba(251,191,36,0.4)" strokeWidth="1" strokeDasharray="5,5" />
        <text x={CHART_W - CHART_PAD_R - 4} y={avgY - 4} textAnchor="end" className="fill-yellow-400/45" style={{ fontSize: 8, fontFamily: 'monospace' }}>avg</text>

        {/* Area fill + smooth animated line */}
        <path d={areaPath} fill={`url(#${fillId})`} />
        <AnimatedLinePath d={linePath} />

        {/* Dots */}
        {data.map((d, i) => {
          const v = mode === 'gross' ? d.gross_multiple : d.net_multiple
          return (
            <g key={d.session_id}>
              <rect x={CHART_PAD_L + bands[i].left} y={CHART_PAD_T} width={bands[i].width} height={chartH} fill="transparent" />
              <GlowDot cx={toX(i)} cy={toY(v)} active={tooltip?.idx === i} />
            </g>
          )
        })}

        {/* X labels */}
        {xLabels.map(d => {
          const i = data.indexOf(d)
          const anchor = i === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle'
          return (
            <text key={d.session_id} x={toX(i)} y={CHART_H - 4} textAnchor={anchor} className="fill-white/20" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {fmtShortDate(d.date)}
            </text>
          )
        })}
      </svg>

      {tooltip && (
        <TooltipShell x={tooltip.x} y={tooltip.y} svgRef={svgRef} width={172}>
          <div className="text-[10px] font-mono text-white/40 mb-1">{fmtShortDate(tooltip.d.date)}</div>
          <div className="font-mono font-bold text-emerald-400" style={{ fontSize: 20, lineHeight: 1.1 }}>
            {(mode === 'gross' ? tooltip.d.gross_multiple : tooltip.d.net_multiple).toFixed(2)}
            <span className="text-[11px] font-medium text-emerald-400/60 ml-1">x/day</span>
          </div>
        </TooltipShell>
      )}
    </div>
  )
}


// ── Sub-tabs ──────────────────────────────────────────────────────────────────

function OverviewTab({ m, strat }: { m: Metrics; strat: StrategyData | null }) {
  const liveNetPnl = m.live_total_pnl - m.live_total_fees
  const hasIssues  = (m.recovering_sessions + m.degraded_sessions) > 0
  const allTimeNetPnl = strat ? strat.total_pnl - strat.total_fees : null

  return (
    <div className="space-y-5">
      <Section title="Overview">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Stat label="Users" value={m.total_users} accent="text-white/70" />
          <Stat
            label="Active Sessions"
            value={m.active_sessions}
            accent={m.active_sessions > 0 ? 'text-emerald-400' : 'text-white/30'}
          />
          <Stat label="Stopping" value={m.stopping_sessions} accent="text-white/40" />
          <Stat label="All Time Sessions" value={m.total_sessions} accent="text-white/40" />
        </div>
      </Section>

      {hasIssues && (
        <Section title="Issues">
          <div className="grid grid-cols-2 gap-2">
            <Stat
              label="Recovering"
              value={m.recovering_sessions}
              accent={m.recovering_sessions > 0 ? 'text-amber-400' : 'text-white/30'}
            />
            <Stat
              label="Degraded / Error"
              value={m.degraded_sessions}
              accent={m.degraded_sessions > 0 ? 'text-red-400' : 'text-white/30'}
            />
          </div>
        </Section>
      )}

      <Section title="Capital">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Stat label="Gross (active)"    value={fmtUsd(m.total_gross_capital, 0)}      sub="session gross_capital" />
          <Stat label="Deployable"        value={fmtUsd(m.total_deployable_capital, 0)} sub="after fee reserve" />
          <Stat
            label="Deployed Now"
            value={fmtUsd(m.total_deployed_capital, 0)}
            accent={m.total_deployed_capital > 0 ? 'text-brand' : 'text-white/30'}
            sub="in open orders"
          />
          <Stat label="Trading Balances"  value={fmtUsd(m.total_trading_balance, 0)}   sub="all Polymarket wallets" />
        </div>
      </Section>

      {m.active_sessions > 0 && (
        <Section title="Live (running sessions)">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Stat label="Volume"    value={fmtUsd(m.live_total_volume, 0)} accent="text-white/65" />
            <Stat label="Gross PnL" value={fmtUsd(m.live_total_pnl)}      accent={pnlColor(m.live_total_pnl)} />
            <Stat label="Fees"      value={fmtUsd(m.live_total_fees)}      accent="text-white/35" />
            <Stat label="Net PnL"   value={fmtUsd(liveNetPnl)}             accent={pnlColor(liveNetPnl)} />
          </div>
        </Section>
      )}

      {strat && allTimeNetPnl !== null && (
        <Section title="All-Time">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Stat label="Volume"    value={fmtUsd(strat.total_volume, 0)} accent="text-white/65" />
            <Stat label="Gross PnL" value={fmtUsd(strat.total_pnl)}      accent={pnlColor(strat.total_pnl)} />
            <Stat label="Fees"      value={fmtUsd(strat.total_fees)}      accent="text-white/35" />
            <Stat label="Net PnL"   value={fmtUsd(allTimeNetPnl)}         accent={pnlColor(allTimeNetPnl)} />
          </div>
        </Section>
      )}
    </div>
  )
}

function VersionsTab({ strat }: { strat: StrategyData | null }) {
  if (!strat) return <div className="p-4 text-white/20 text-xs font-mono">No data.</div>

  return (
    <div className="space-y-5">
      <Section title="All-Time Strategy">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Stat label="Total Sessions"  value={strat.total_sessions}             accent="text-white/55" />
          <Stat label="Total Volume"    value={fmtUsd(strat.total_volume, 0)}    accent="text-white/65" />
          <Stat label="Total PnL"       value={fmtUsd(strat.total_pnl)}          accent={pnlColor(strat.total_pnl)} />
          <Stat label="Volume Multiple" value={`${strat.volume_multiple}×`}      accent="text-white/55" />
        </div>
      </Section>

      {strat.versions.length > 0 && (
        <Section title="Versions">
          <div className="rounded-xl border border-white/[0.05] overflow-hidden overflow-x-auto">
            <div className="grid grid-cols-5 gap-2 px-4 py-2 bg-white/[0.015] min-w-[400px]">
              {['Version', 'Sessions', 'Volume', 'PnL', 'Fees'].map(h => (
                <div key={h} className="text-[9px] font-semibold uppercase tracking-widest text-white/20">{h}</div>
              ))}
            </div>
            {strat.versions.map(v => (
              <div key={v.version_id} className="grid grid-cols-5 gap-2 px-4 py-2.5 border-t border-white/[0.04] hover:bg-white/[0.02] min-w-[400px]">
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] font-mono text-brand">{v.version_label || v.version_id.slice(0, 8)}</span>                </div>
                <div className="text-[11px] font-mono text-white/40">{v.session_count}</div>
                <div className="text-[11px] font-mono text-white/50 tabular-nums">{fmtUsd(v.total_volume, 0)}</div>
                <div className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(v.total_pnl)}`}>{fmtUsd(v.total_pnl)}</div>
                <div className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(v.total_fees)}</div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {strat.markets.length > 0 && (
        <Section title={`Top Markets (${strat.markets.length})`}>
          <div className="rounded-xl border border-white/[0.05] overflow-hidden overflow-x-auto">
            <div className="grid grid-cols-5 gap-2 px-4 py-2 bg-white/[0.015] min-w-[480px]">
              {['Market', 'Volume', 'PnL', 'Fees', 'Runs'].map(h => (
                <div key={h} className="text-[9px] font-semibold uppercase tracking-widest text-white/20">{h}</div>
              ))}
            </div>
            {strat.markets.slice(0, 20).map(mkt => (
              <div key={mkt.polymarket_url} className="grid grid-cols-5 gap-2 px-4 py-2.5 border-t border-white/[0.04] hover:bg-white/[0.02] min-w-[480px]">
                <div className="text-[10px] font-mono text-white/45 truncate pr-2" title={mkt.question}>{mkt.question || mkt.polymarket_url}</div>
                <div className="text-[10px] font-mono text-white/50 tabular-nums">{fmtUsd(mkt.volume, 0)}</div>
                <div className={`text-[10px] font-mono font-semibold tabular-nums ${pnlColor(mkt.realized_pnl)}`}>{fmtUsd(mkt.realized_pnl)}</div>
                <div className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(mkt.fees_paid)}</div>
                <div className="text-[10px] font-mono text-white/30">{mkt.exposure_count}</div>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}

const DAYS_OPTIONS = [7, 14, 30, 60, 90] as const
type DaysOption = typeof DAYS_OPTIONS[number]

function DaysToggle({ days, setDays }: { days: DaysOption; setDays: (d: DaysOption) => void }) {
  return (
    <div className="flex gap-1">
      {DAYS_OPTIONS.map(d => (
        <button
          key={d}
          onClick={() => setDays(d)}
          className={`px-2 py-1 rounded text-[9px] font-semibold uppercase tracking-widest transition-colors ${
            days === d ? 'bg-brand/20 text-brand/80' : 'text-white/25 hover:text-white/50 hover:bg-white/[0.05]'
          }`}
        >
          {d}d
        </button>
      ))}
    </div>
  )
}

function GrossNetToggle({ mode, setMode }: { mode: 'gross' | 'net'; setMode: (m: 'gross' | 'net') => void }) {
  return (
    <div className="flex gap-1">
      {(['gross', 'net'] as const).map(m => (
        <button
          key={m}
          onClick={() => setMode(m)}
          className={`px-2 py-1 rounded text-[9px] font-semibold uppercase tracking-widest transition-colors ${
            mode === m ? 'bg-brand/20 text-brand/80' : 'text-white/25 hover:text-white/50 hover:bg-white/[0.05]'
          }`}
        >
          {m}
        </button>
      ))}
    </div>
  )
}

function ChartCard({ title, loading, children, controls }: {
  title: string; loading: boolean; children: React.ReactNode; controls?: React.ReactNode
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 justify-between">
        <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20">{title}</div>
        {controls && <div className="flex flex-wrap gap-1">{controls}</div>}
      </div>
      <div className="bg-[#0d0e10] border border-white/[0.05] rounded-xl px-2 sm:px-4 pt-4 pb-2 overflow-x-auto">
        {loading ? (
          <div className="h-[200px] flex items-center justify-center">
            <div className="w-24 h-2 bg-white/[0.04] rounded animate-pulse" />
          </div>
        ) : children}
      </div>
    </div>
  )
}

function AnalyticsTab() {
  const [volDays,  setVolDays]  = useState<DaysOption>(60)
  const [cpmDays,  setCpmDays]  = useState<DaysOption>(30)
  const [multDays, setMultDays] = useState<DaysOption>(60)
  const [cpmMode,  setCpmMode]  = useState<'gross' | 'net'>('gross')
  const [multMode, setMultMode] = useState<'gross' | 'net'>('gross')

  const [volData,  setVolData]  = useState<DayVolume[]>([])
  const [cpmData,  setCpmData]  = useState<DayCpm[]>([])
  const [multData, setMultData] = useState<SessionMultiple[]>([])

  const [volLoad,  setVolLoad]  = useState(true)
  const [cpmLoad,  setCpmLoad]  = useState(true)
  const [multLoad, setMultLoad] = useState(true)

  useEffect(() => {
    setVolLoad(true)
    ;(api.admin.dailyVolume(volDays) as Promise<DayVolume[]>)
      .then(setVolData).catch(() => setVolData([]))
      .finally(() => setVolLoad(false))
  }, [volDays])

  useEffect(() => {
    setCpmLoad(true)
    ;(api.admin.dailyCpm(cpmDays) as Promise<DayCpm[]>)
      .then(setCpmData).catch(() => setCpmData([]))
      .finally(() => setCpmLoad(false))
  }, [cpmDays])

  useEffect(() => {
    setMultLoad(true)
    ;(api.admin.dailyMultiple(multDays) as Promise<SessionMultiple[]>)
      .then(rows => setMultData(groupMultipleByDate(rows))).catch(() => setMultData([]))
      .finally(() => setMultLoad(false))
  }, [multDays])

  return (
    <div className="space-y-6">
      <ChartCard
        title="Daily Volume + Fees"
        loading={volLoad}
        controls={<DaysToggle days={volDays} setDays={setVolDays} />}
      >
        <VolumeChart data={volData} />
      </ChartCard>

      <ChartCard
        title="CPM Performance"
        loading={cpmLoad}
        controls={<><GrossNetToggle mode={cpmMode} setMode={setCpmMode} /><DaysToggle days={cpmDays} setDays={setCpmDays} /></>}
      >
        <CpmChart data={cpmData} mode={cpmMode} />
      </ChartCard>

      <ChartCard
        title="Daily Volume Multiple"
        loading={multLoad}
        controls={<><GrossNetToggle mode={multMode} setMode={setMultMode} /><DaysToggle days={multDays} setDays={setMultDays} /></>}
      >
        <MultipleChart data={multData} mode={multMode} />
      </ChartCard>
    </div>
  )
}

// ── Referrals Tab ─────────────────────────────────────────────────────────────

function ReferralsTab() {
  const [rows, setRows]       = useState<ReferrerRow[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    api.admin.referralLeaderboard().then((data: any) => {
      setRows(data as ReferrerRow[])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-white/20 text-xs animate-pulse">Loading…</div>
  if (!rows.length) return <div className="text-white/20 text-xs">No referrals yet.</div>

  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_60px_110px_110px] gap-2 px-3 py-1.5 text-[9px] font-semibold uppercase tracking-widest text-white/20">
        <span>Referrer</span>
        <span className="text-right">Filleuls</span>
        <span className="text-right">Vol. apporté</span>
        <span className="text-right">Gains</span>
      </div>
      {rows.map((r, i) => (
        <div key={r.referrer_id}>
          <button
            onClick={() => setExpanded(expanded === r.referrer_id ? null : r.referrer_id)}
            className="w-full grid grid-cols-[1fr_60px_110px_110px] gap-2 px-3 py-2.5 bg-[#0d0e10] border border-white/[0.05] rounded-xl hover:border-white/10 transition-colors text-left"
          >
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-[10px] font-mono text-white/20 w-5 flex-shrink-0">#{i + 1}</span>
              <span className="text-[12px] font-semibold text-white/80 truncate">{r.display_name}</span>
            </div>
            <span className="text-right text-[12px] font-mono text-white/50">{r.referral_count}</span>
            <span className="text-right text-[12px] font-mono text-white/70">{fmtUsd(r.referred_volume)}</span>
            <span className="text-right text-[12px] font-mono text-emerald-400/80">{fmtUsd(r.referrer_earnings)}</span>
          </button>
          {expanded === r.referrer_id && r.referrals.length > 0 && (
            <div className="ml-7 mt-1 mb-1 border-l border-white/[0.05] pl-3 space-y-0.5">
              <div className="grid grid-cols-[1fr_110px_110px] gap-2 px-2 py-1 text-[9px] font-semibold uppercase tracking-widest text-white/15">
                <span>Filleul</span>
                <span className="text-right">Volume</span>
                <span className="text-right">Parrain gagne</span>
              </div>
              {r.referrals.map(u => (
                <div key={u.user_id} className="grid grid-cols-[1fr_110px_110px] gap-2 px-2 py-1.5 bg-white/[0.02] rounded-lg">
                  <span className="text-[11px] text-white/50 truncate">{u.display_name}</span>
                  <span className="text-right text-[11px] font-mono text-white/40">{fmtUsd(u.volume)}</span>
                  <span className="text-right text-[11px] font-mono text-emerald-400/50">{fmtUsd(u.referrer_earned)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Main Dashboard ─────────────────────────────────────────────────────────────

export default function AdminDashboard() {
  const [m, setM]           = useState<Metrics | null>(null)
  const [strat, setStrat]   = useState<StrategyData | null>(null)
  const [loading, setLoad]  = useState(true)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [subTab, setSubTab] = useState<SubTab>('overview')

  const load = useCallback(async () => {
    try {
      const [metrics, analytics] = await Promise.all([
        api.admin.globalMetrics() as Promise<Metrics>,
        api.admin.globalStrategyAnalytics() as Promise<{ summary: any; versions: VersionPerf[]; markets: MarketPerf[] }>,
      ])
      setM(metrics)
      if (analytics?.summary) {
        setStrat({
          ...analytics.summary,
          versions: analytics.versions || [],
          markets:  analytics.markets  || [],
        })
      }
      setLastRefresh(new Date())
    } catch {}
    setLoad(false)
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, 30_000)
    return () => clearInterval(interval)
  }, [load])

  const hasIssues = m ? (m.recovering_sessions + m.degraded_sessions) > 0 : false

  return (
    <div className="flex flex-col min-h-screen">

      {/* Sticky top bar — full bleed */}
      <div className="sticky top-11 md:top-0 z-20 bg-[#0a0b0d] border-b border-white/[0.06]">
        <div className="flex items-center justify-between px-5 h-11">
          <div className="flex items-center gap-3">
            <span className="text-[11px] font-bold text-white/70 uppercase tracking-widest">Dashboard</span>
            {hasIssues && (
              <span className="text-[10px] font-mono text-amber-400/70 flex items-center">
                <PulseDot color="bg-amber-400" />
                {m!.recovering_sessions + m!.degraded_sessions} issue{(m!.recovering_sessions + m!.degraded_sessions) > 1 ? 's' : ''}
              </span>
            )}
          </div>
          {lastRefresh && (
            <button
              onClick={load}
              className="text-[9px] text-white/20 hover:text-white/50 transition-colors font-mono"
            >
              ↻ {lastRefresh.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </button>
          )}
        </div>

        {/* Sub-tab nav */}
        <div className="flex px-5 border-t border-white/[0.04]">
          {(['overview', 'versions', 'analytics', 'referrals'] as SubTab[]).map(t => (
            <button
              key={t}
              onClick={() => setSubTab(t)}
              className={`px-4 py-2 text-[10px] font-semibold uppercase tracking-widest transition-colors relative ${
                subTab === t ? 'text-white' : 'text-white/25 hover:text-white/50'
              }`}
            >
              {t}
              {subTab === t && <span className="absolute bottom-0 left-0 right-0 h-px bg-brand" />}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 p-4 sm:p-6 space-y-5">
        {loading ? (
          <div className="space-y-3">
            {[1,2,3,4].map(i => (
              <div key={i} className="h-16 bg-white/[0.02] rounded-xl animate-pulse" />
            ))}
          </div>
        ) : !m ? (
          <div className="text-white/20 text-xs">Failed to load metrics.</div>
        ) : (
          <>
            {subTab === 'overview'   && <OverviewTab m={m} strat={strat} />}
            {subTab === 'versions'   && <VersionsTab strat={strat} />}
            {subTab === 'analytics'  && <AnalyticsTab />}
            {subTab === 'referrals'  && <ReferralsTab />}
          </>
        )}
      </div>
    </div>
  )
}
