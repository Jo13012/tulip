import { useEffect, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import { api } from '../../utils/api'
import { fmtUsd, pnlColor } from '../../utils/format'

interface DayCpm { date: string; volume: number; gross_cpm: number | null; net_cpm: number | null }
interface SessionMultiple {
  session_id: string
  date: string
  volume: number
  run_days: number
  capital: number
  gross_multiple: number
  net_multiple: number
  gross_pnl: number
  fees: number
}

const CHART_PAD_L = 52
const CHART_PAD_R = 16
const CHART_PAD_T = 20
const CHART_PAD_B = 30
const CHART_W = 900
const CHART_H = 200
const DAYS_OPTIONS = [7, 14, 30, 60, 90] as const
type DaysOption = typeof DAYS_OPTIONS[number]

function fmtShortDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

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
    }
    current.volume += row.volume || 0
    current.gross_pnl += row.gross_pnl || 0
    current.fees += row.fees || 0
    current.capital += row.capital || 0
    current.capital_days += capitalDays
    current.run_days_weighted += capitalDays
    grouped.set(row.date, current)
  }

  return Array.from(grouped.values())
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((row) => {
      const grossMultiple = row.capital_days > 0 ? row.volume / row.capital_days : 0
      const netMultiple = row.capital_days > 0 ? (row.volume - row.fees) / row.capital_days : 0
      const avgRunDays = row.capital > 0 ? row.run_days_weighted / row.capital : 0
      return {
        session_id: `group:${row.date}`,
        date: row.date,
        volume: Number(row.volume.toFixed(2)),
        run_days: Number(avgRunDays.toFixed(3)),
        capital: Number(row.capital.toFixed(2)),
        gross_multiple: Number(grossMultiple.toFixed(4)),
        net_multiple: Number(netMultiple.toFixed(4)),
        gross_pnl: Number(row.gross_pnl.toFixed(2)),
        fees: Number(row.fees.toFixed(2)),
      }
    })
}

function catmullRom(points: { x: number; y: number }[], tension = 0.35): string {
  if (points.length < 2) return ''
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

function AnimatedLinePath({ d, color = 'rgba(52,211,153,' }: { d: string; color?: string }) {
  const ref = useRef<SVGPathElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const len = el.getTotalLength?.() ?? 600
    el.style.strokeDasharray = String(len)
    el.style.strokeDashoffset = String(len)
    el.style.transition = 'none'
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.style.transition = 'stroke-dashoffset 1.4s cubic-bezier(0.4,0,0.2,1)'
        el.style.strokeDashoffset = '0'
      })
    })
  }, [d])
  return (
    <>
      {/* single soft glow — thin, low opacity */}
      <path d={d} stroke={`${color}0.12)`} strokeWidth={6} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      {/* crisp line */}
      <path ref={ref} d={d} stroke={`${color}0.75)`} strokeWidth={1.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </>
  )
}

function GlowDot({ cx, cy, active, color = 'rgba(52,211,153,' }: { cx: number; cy: number; active: boolean; color?: string }) {
  return (
    <g>
      {active && (
        <circle cx={cx} cy={cy} r={10} fill={`${color}1)`} opacity={0.07} />
      )}
      <circle
        cx={cx}
        cy={cy}
        r={active ? 3.5 : 2}
        fill={active ? `${color}0.9)` : `${color}0.35)`}
        stroke={active ? 'rgba(255,255,255,0.5)' : 'none'}
        strokeWidth={active ? 1 : 0}
        style={{ transition: 'r 0.15s, opacity 0.15s' }}
      />
    </g>
  )
}

function TooltipShell({
  x, y, children, svgRef, width = 140,
}: {
  x: number
  y: number
  children: ReactNode
  svgRef: RefObject<SVGSVGElement>
  width?: number
}) {
  const heightEst = 80
  const [pos, setPos] = useState({ tx: 0, ty: 0 })

  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const svgW = el.clientWidth
    const svgH = el.clientHeight
    let tx = x + 16
    let ty = y - heightEst / 2
    if (tx + width > svgW - 8) tx = x - width - 16
    if (ty < 8) ty = 8
    if (ty + heightEst > svgH - 8) ty = svgH - heightEst - 8
    setPos({ tx, ty })
  }, [x, y, svgRef, width])

  return (
    <div
      style={{
        position: 'absolute',
        left: pos.tx,
        top: pos.ty,
        width,
        pointerEvents: 'none',
        zIndex: 20,
        transition: 'left 0.05s, top 0.05s',
      }}
    >
      <div style={{
        background: 'rgba(8,9,14,0.97)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: 12,
        padding: '10px 13px',
        backdropFilter: 'blur(20px)',
        boxShadow: '0 12px 40px rgba(0,0,0,0.7), inset 0 1px 0 rgba(255,255,255,0.04)',
      }}>
        {children}
      </div>
    </div>
  )
}

function DaysToggle({ days, setDays }: { days: DaysOption; setDays: (d: DaysOption) => void }) {
  return (
    <div className="flex gap-0.5 bg-white/[0.03] rounded-lg p-0.5">
      {DAYS_OPTIONS.map(d => (
        <button
          key={d}
          onClick={() => setDays(d)}
          className={`px-2.5 py-1 rounded-md text-[9px] font-semibold uppercase tracking-widest transition-all ${
            days === d
              ? 'bg-white/[0.08] text-white/70 shadow-sm'
              : 'text-white/20 hover:text-white/40'
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
    <div className="flex gap-0.5 bg-white/[0.03] rounded-lg p-0.5">
      {(['gross', 'net'] as const).map(m => (
        <button
          key={m}
          onClick={() => setMode(m)}
          className={`px-2.5 py-1 rounded-md text-[9px] font-semibold uppercase tracking-widest transition-all ${
            mode === m
              ? 'bg-white/[0.08] text-white/70 shadow-sm'
              : 'text-white/20 hover:text-white/40'
          }`}
        >
          {m}
        </button>
      ))}
    </div>
  )
}

function ChartSkeleton() {
  return (
    <div className="relative h-[200px] overflow-hidden rounded-lg">
      <div className="absolute inset-0 flex flex-col justify-between py-4 px-3 opacity-30">
        {[0,1,2,3,4].map(i => (
          <div key={i} className="h-px bg-white/[0.06] w-full" />
        ))}
      </div>
      <div className="absolute inset-0 flex items-end gap-1 px-12 pb-6">
        {Array.from({ length: 18 }, (_, i) => (
          <div
            key={i}
            className="flex-1 bg-white/[0.03] rounded-t-sm animate-pulse"
            style={{ height: `${20 + Math.sin(i * 0.8) * 30 + Math.random() * 20}%`, animationDelay: `${i * 50}ms` }}
          />
        ))}
      </div>
    </div>
  )
}

function ChartCard({ title, loading, children, controls, hero, note }: {
  title: string
  loading: boolean
  children: ReactNode
  controls?: ReactNode
  hero?: string
  note?: string
}) {
  return (
    <div className="rounded-2xl border border-white/[0.05] bg-[#0a0b0e] px-3 sm:px-5 pt-5 pb-3 overflow-x-hidden">
      <div className="flex flex-wrap items-start gap-3 justify-between mb-4 px-0.5">
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20">{title}</div>
          {hero && <div className="text-[11px] text-white/30 mt-1 leading-relaxed">{hero}</div>}
        </div>
        {controls && <div className="flex flex-wrap items-center gap-2">{controls}</div>}
      </div>
      {loading ? <ChartSkeleton /> : children}
      {note && !loading && (
        <div className="mt-2 px-1 text-[9px] font-mono text-white/15 leading-relaxed">{note}</div>
      )}
    </div>
  )
}

function ZeroLine({ y, chartW }: { y: number; chartW: number }) {
  if (y <= CHART_PAD_T || y >= CHART_PAD_T + CHART_H - CHART_PAD_B) return null
  return (
    <line
      x1={CHART_PAD_L}
      y1={y}
      x2={CHART_W - CHART_PAD_R}
      y2={y}
      stroke="rgba(255,255,255,0.12)"
      strokeWidth="1"
      strokeDasharray="3,4"
    />
  )
}

function CpmChart({ data, mode }: { data: DayCpm[]; mode: 'gross' | 'net' }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; d: DayCpm; idx: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const pts = data.filter(d => (mode === 'gross' ? d.gross_cpm : d.net_cpm) !== null) as DayCpm[]

  if (!pts.length) {
    return (
      <div className="flex items-center justify-center h-44 text-white/15 text-[10px] font-mono uppercase tracking-widest">
        No data yet
      </div>
    )
  }

  const vals = pts.map(d => (mode === 'gross' ? d.gross_cpm! : d.net_cpm!))
  const minV = Math.min(...vals)
  const maxV = Math.max(...vals)
  const avg = vals.reduce((sum, v) => sum + v, 0) / vals.length
  const pad = (maxV - minV) * 0.12 || 50
  const domainMin = minV - pad
  const domainMax = maxV + pad
  const rangeV = domainMax - domainMin || 1
  const chartW = CHART_W - CHART_PAD_L - CHART_PAD_R
  const chartH = CHART_H - CHART_PAD_T - CHART_PAD_B
  const bands = buildIndexBands(pts.length, chartW)
  const toX = (i: number) => CHART_PAD_L + bands[i].center
  const toY = (v: number) => CHART_PAD_T + chartH - ((v - domainMin) / rangeV) * chartH
  const avgY = toY(avg)
  const zeroY = toY(0)

  const yTicks = Array.from({ length: 5 }, (_, i) => {
    const v = domainMin + (rangeV * i / 4)
    return { v, y: toY(v) }
  })

  const step = Math.max(1, Math.floor(pts.length / 7))
  const xLabels = pts.filter((_, i) => i % step === 0 || i === pts.length - 1)
  const splinePts = pts.map((d, i) => ({ x: toX(i), y: toY(mode === 'gross' ? d.gross_cpm! : d.net_cpm!) }))
  const linePath = catmullRom(splinePts)
  const areaPath = linePath
    + ` L${toX(pts.length - 1).toFixed(2)},${CHART_PAD_T + chartH}`
    + ` L${toX(0).toFixed(2)},${CHART_PAD_T + chartH} Z`

  const fillId = `user-cpm-area-${mode}`
  const positiveColor = 'rgba(52,211,153,'
  const negativeColor = 'rgba(239,68,68,'
  const isPositiveAvg = avg >= 0

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
            <stop offset="0%" stopColor={isPositiveAvg ? 'rgba(52,211,153,0.22)' : 'rgba(239,68,68,0.18)'} />
            <stop offset="45%" stopColor={isPositiveAvg ? 'rgba(52,211,153,0.07)' : 'rgba(239,68,68,0.05)'} />
            <stop offset="100%" stopColor="rgba(52,211,153,0)" />
          </linearGradient>
        </defs>

        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={CHART_PAD_L} y1={t.y} x2={CHART_W - CHART_PAD_R} y2={t.y} stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
            <text x={CHART_PAD_L - 6} y={t.y + 3.5} textAnchor="end" fill="rgba(255,255,255,0.18)" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {Math.round(t.v)}
            </text>
          </g>
        ))}

        <ZeroLine y={zeroY} chartW={chartW} />

        {tooltip && (
          <>
            <rect
              x={CHART_PAD_L + bands[tooltip.idx].left}
              y={CHART_PAD_T}
              width={bands[tooltip.idx].width}
              height={chartH}
              rx={4}
              fill="rgba(255,255,255,0.018)"
            />
            <line
              x1={toX(tooltip.idx)}
              y1={CHART_PAD_T}
              x2={toX(tooltip.idx)}
              y2={CHART_PAD_T + chartH}
              stroke="rgba(255,255,255,0.08)"
              strokeWidth="1"
              strokeDasharray="3,5"
            />
          </>
        )}

        {/* avg line */}
        <line x1={CHART_PAD_L} y1={avgY} x2={CHART_W - CHART_PAD_R} y2={avgY}
          stroke="rgba(251,191,36,0.3)" strokeWidth="1" strokeDasharray="4,5" />
        <text x={CHART_W - CHART_PAD_R - 4} y={avgY - 4} textAnchor="end" fill="rgba(251,191,36,0.4)" style={{ fontSize: 8, fontFamily: 'monospace' }}>
          avg {Math.round(avg)}
        </text>

        <path d={areaPath} fill={`url(#${fillId})`} />
        <AnimatedLinePath d={linePath} color={isPositiveAvg ? positiveColor : negativeColor} />

        {pts.map((d, i) => {
          const v = mode === 'gross' ? d.gross_cpm! : d.net_cpm!
          return (
            <g key={`${d.date}-${i}`}>
              <rect x={CHART_PAD_L + bands[i].left} y={CHART_PAD_T} width={bands[i].width} height={chartH} fill="transparent" />
              <GlowDot cx={toX(i)} cy={toY(v)} active={tooltip?.idx === i} color={v >= 0 ? positiveColor : negativeColor} />
            </g>
          )
        })}

        {xLabels.map(d => {
          const i = pts.indexOf(d)
          const anchor = i === 0 ? 'start' : i === pts.length - 1 ? 'end' : 'middle'
          return (
            <text key={`${d.date}-${i}`} x={toX(i)} y={CHART_H - 4} textAnchor={anchor} fill="rgba(255,255,255,0.18)" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {fmtShortDate(d.date)}
            </text>
          )
        })}
      </svg>

      {tooltip && (
        <TooltipShell x={tooltip.x} y={tooltip.y} svgRef={svgRef} width={155}>
          <div style={{ fontSize: 9, fontFamily: 'monospace', color: 'rgba(255,255,255,0.35)', marginBottom: 6 }}>
            {fmtShortDate(tooltip.d.date)}
          </div>
          <div style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 22, lineHeight: 1.1 }}
            className={pnlColor(mode === 'gross' ? tooltip.d.gross_cpm! : tooltip.d.net_cpm!)}
          >
            {Math.round(mode === 'gross' ? tooltip.d.gross_cpm! : tooltip.d.net_cpm!)}
            <span style={{ fontSize: 11, fontWeight: 500, marginLeft: 4, color: 'rgba(52,211,153,0.55)' }}>CPM</span>
          </div>
        </TooltipShell>
      )}
    </div>
  )
}

function MultipleChart({ data, mode }: { data: SessionMultiple[]; mode: 'gross' | 'net' }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; d: SessionMultiple; idx: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-44 text-white/15 text-[10px] font-mono uppercase tracking-widest">
        No data yet
      </div>
    )
  }

  const vals = data.map(d => mode === 'gross' ? d.gross_multiple : d.net_multiple)
  const minV = Math.min(...vals, 0)
  const rawMax = Math.max(...vals, 0.1)
  const avg = vals.reduce((sum, v) => sum + v, 0) / vals.length
  // Soft-cap extreme outliers: if max > 3× avg, clamp to avg * 3 so chart isn't crushed by one spike
  const maxV = avg > 0 && rawMax > avg * 3 ? avg * 3 : rawMax
  const pad = (maxV - minV) * 0.12
  const domainMin = Math.max(0, minV - pad)
  const domainMax = maxV + pad
  const rangeV = domainMax - domainMin || 1
  const chartW = CHART_W - CHART_PAD_L - CHART_PAD_R
  const chartH = CHART_H - CHART_PAD_T - CHART_PAD_B
  const bands = buildIndexBands(data.length, chartW)
  const toX = (i: number) => CHART_PAD_L + bands[i].center
  const toY = (v: number) => CHART_PAD_T + chartH - ((v - domainMin) / rangeV) * chartH
  const avgY = toY(avg)

  const yTicks = Array.from({ length: 5 }, (_, i) => {
    const v = domainMin + (rangeV * i / 4)
    return { v, y: toY(v) }
  })

  const step = data.length <= 12 ? 1 : Math.max(1, Math.floor(data.length / 7))
  const xLabels = data.filter((_, i) => i % step === 0 || i === data.length - 1)
  const splinePts = data.map((d, i) => ({
    x: toX(i),
    y: toY(mode === 'gross' ? d.gross_multiple : d.net_multiple),
  }))
  const linePath = catmullRom(splinePts)
  const areaPath = linePath
    + ` L${toX(data.length - 1).toFixed(2)},${CHART_PAD_T + chartH}`
    + ` L${toX(0).toFixed(2)},${CHART_PAD_T + chartH} Z`

  const fillId = `user-multiple-area-${mode}`
  const lineColor = 'rgba(52,211,153,'

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
            <stop offset="0%" stopColor="rgba(52,211,153,0.22)" />
            <stop offset="45%" stopColor="rgba(52,211,153,0.07)" />
            <stop offset="100%" stopColor="rgba(52,211,153,0)" />
          </linearGradient>
        </defs>

        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={CHART_PAD_L} y1={t.y} x2={CHART_W - CHART_PAD_R} y2={t.y} stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
            <text x={CHART_PAD_L - 6} y={t.y + 3.5} textAnchor="end" fill="rgba(255,255,255,0.18)" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {t.v.toFixed(1)}x
            </text>
          </g>
        ))}

        {tooltip && (
          <>
            <rect
              x={CHART_PAD_L + bands[tooltip.idx].left}
              y={CHART_PAD_T}
              width={bands[tooltip.idx].width}
              height={chartH}
              rx={4}
              fill="rgba(255,255,255,0.018)"
            />
            <line
              x1={toX(tooltip.idx)}
              y1={CHART_PAD_T}
              x2={toX(tooltip.idx)}
              y2={CHART_PAD_T + chartH}
              stroke="rgba(255,255,255,0.08)"
              strokeWidth="1"
              strokeDasharray="3,5"
            />
          </>
        )}

        {/* avg line */}
        <line x1={CHART_PAD_L} y1={avgY} x2={CHART_W - CHART_PAD_R} y2={avgY}
          stroke="rgba(251,191,36,0.3)" strokeWidth="1" strokeDasharray="4,5" />
        <text x={CHART_W - CHART_PAD_R - 4} y={avgY - 4} textAnchor="end" fill="rgba(251,191,36,0.4)" style={{ fontSize: 8, fontFamily: 'monospace' }}>
          avg {avg.toFixed(1)}x
        </text>

        <path d={areaPath} fill={`url(#${fillId})`} />
        <AnimatedLinePath d={linePath} color={lineColor} />

        {data.map((d, i) => {
          const v = mode === 'gross' ? d.gross_multiple : d.net_multiple
          return (
            <g key={d.session_id}>
              <rect x={CHART_PAD_L + bands[i].left} y={CHART_PAD_T} width={bands[i].width} height={chartH} fill="transparent" />
              <GlowDot cx={toX(i)} cy={toY(v)} active={tooltip?.idx === i} color={lineColor} />
            </g>
          )
        })}

        {xLabels.map(d => {
          const i = data.indexOf(d)
          const anchor = i === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle'
          return (
            <text key={d.session_id} x={toX(i)} y={CHART_H - 4} textAnchor={anchor} fill="rgba(255,255,255,0.18)" style={{ fontSize: 9, fontFamily: 'monospace' }}>
              {fmtShortDate(d.date)}
            </text>
          )
        })}
      </svg>

      {tooltip && (
        <TooltipShell x={tooltip.x} y={tooltip.y} svgRef={svgRef} width={178}>
          <div style={{ fontSize: 9, fontFamily: 'monospace', color: 'rgba(255,255,255,0.35)', marginBottom: 6 }}>
            {fmtShortDate(tooltip.d.date)}
          </div>
          <div style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 22, lineHeight: 1.1, color: 'rgba(52,211,153,0.95)' }}>
            {(mode === 'gross' ? tooltip.d.gross_multiple : tooltip.d.net_multiple).toFixed(2)}
            <span style={{ fontSize: 11, fontWeight: 500, marginLeft: 4, color: 'rgba(52,211,153,0.5)' }}>× / day</span>
          </div>
        </TooltipShell>
      )}
    </div>
  )
}

export function PerformanceTab() {
  const [cpmDays, setCpmDays] = useState<DaysOption>(30)
  const [multDays, setMultDays] = useState<DaysOption>(60)
  const [cpmMode, setCpmMode] = useState<'gross' | 'net'>('gross')
  const [multMode, setMultMode] = useState<'gross' | 'net'>('gross')

  const [cpmData, setCpmData] = useState<DayCpm[]>([])
  const [multData, setMultData] = useState<SessionMultiple[]>([])

  const [cpmLoad, setCpmLoad] = useState(true)
  const [multLoad, setMultLoad] = useState(true)

  useEffect(() => {
    setCpmLoad(true)
    ;(api.getUserDailyCpm(cpmDays) as Promise<DayCpm[]>)
      .then(setCpmData).catch(() => setCpmData([]))
      .finally(() => setCpmLoad(false))
  }, [cpmDays])

  useEffect(() => {
    setMultLoad(true)
    ;(api.getUserDailyMultiple(multDays) as Promise<SessionMultiple[]>)
      .then(rows => setMultData(groupMultipleByDate(rows))).catch(() => setMultData([]))
      .finally(() => setMultLoad(false))
  }, [multDays])

  return (
    <div className="p-4 sm:p-5 space-y-4">
      <ChartCard
        title="CPM Performance"
        hero="Net profit generated per $1M traded"
        loading={cpmLoad}
        controls={<><GrossNetToggle mode={cpmMode} setMode={setCpmMode} /><DaysToggle days={cpmDays} setDays={setCpmDays} /></>}
      >
        <CpmChart data={cpmData} mode={cpmMode} />
      </ChartCard>

      <ChartCard
        title="Daily Capital Efficiency"
        hero="Daily volume turnover relative to deployed capital."
        loading={multLoad}
        controls={<><GrossNetToggle mode={multMode} setMode={setMultMode} /><DaysToggle days={multDays} setDays={setMultDays} /></>}
      >
        <MultipleChart data={multData} mode={multMode} />
      </ChartCard>
    </div>
  )
}
