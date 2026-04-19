import { useEffect } from 'react'
import { XP_LEVELS } from '../../types'
import { fmtStoredBpsPct } from '../../utils/format'

interface Props {
  trading_volume: number
  fee_bps: number
  fee_is_overridden?: boolean
  onClose: () => void
}

const COLORS = ['#60a5fa','#38bdf8','#34d399','#a3e635','#facc15','#fb923c','#f87171','#c084fc','#f472b6','#e879f9']

function fmtVol(v: number) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

export function XPModal({ trading_volume, fee_bps, fee_is_overridden, onClose }: Props) {
  let curIdx = 0
  for (let i = 0; i < XP_LEVELS.length; i++) {
    if (trading_volume >= XP_LEVELS[i].volume) curIdx = i
    else break
  }

  const cur  = XP_LEVELS[curIdx]
  const next = XP_LEVELS[curIdx + 1] ?? null
  const pct  = next
    ? Math.min(100, ((trading_volume - cur.volume) / (next.volume - cur.volume)) * 100)
    : 100

  const curColor = COLORS[curIdx]

  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

      <div
        className="relative z-10 w-full max-w-2xl bg-[#111214] border border-white/[0.08] rounded-2xl shadow-2xl flex overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Close button — top right of whole modal */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 z-20 w-7 h-7 flex items-center justify-center rounded-lg text-white/25 hover:text-white/60 hover:bg-white/[0.06] transition-colors"
        >
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
            <path d="M1 1l9 9M10 1L1 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </button>

        {/* LEFT — tiers list (70%) */}
        <div className="flex-1 min-w-0">
          {/* Header */}
          <div className="flex items-center px-5 py-4 border-b border-white/[0.06]">
            <p className="text-sm font-bold text-white">Fee tiers</p>
          </div>

          {/* Column headers */}
          <div className="grid grid-cols-[2rem_1fr_4rem] gap-x-3 px-5 py-2 border-b border-white/[0.04]">
            {['Tier', 'Progress', 'Fee'].map((h, i) => (
              <span key={i} className={`text-[9px] uppercase tracking-widest text-white/20 font-semibold ${i >= 2 ? 'text-right' : ''}`}>
                {h}
              </span>
            ))}
          </div>

          {/* Rows */}
          <div className="divide-y divide-white/[0.03]">
            {XP_LEVELS.map((lvl, i) => {
              const past    = i < curIdx
              const current = i === curIdx
              const future  = i > curIdx
              const c       = COLORS[i]
              const rowPct  = past ? 100 : current ? pct : 0

              return (
                <div
                  key={i}
                  className={`grid grid-cols-[2rem_1fr_4rem] gap-x-3 px-5 py-2.5 items-center ${current ? 'bg-white/[0.03]' : ''}`}
                >
                  {/* Tier */}
                  <span
                    className="text-[11px] font-bold"
                    style={{ color: future ? '#ffffff14' : c }}
                  >
                    {lvl.level}
                  </span>

                  {/* Bar + range labels */}
                  <div className="flex flex-col gap-1 min-w-0 py-0.5">
                    <div className="w-full h-[4px] rounded-full bg-white/[0.10] overflow-hidden">
                      {!future && (
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${rowPct}%`,
                            background: c,
                            boxShadow: current ? `0 0 6px ${c}60` : 'none',
                          }}
                        />
                      )}
                    </div>
                    <div className="flex justify-between">
                      <span className="text-[9px] font-mono" style={{ color: future ? '#ffffff12' : '#ffffff35' }}>
                        {fmtVol(lvl.volume)}
                      </span>
                      {XP_LEVELS[i + 1] && (
                        <span className="text-[9px] font-mono" style={{ color: future ? '#ffffff12' : '#ffffff35' }}>
                          {fmtVol(XP_LEVELS[i + 1].volume)}
                        </span>
                      )}
                    </div>
                  </div>


                  {/* Fee */}
              <span
                    className="text-[12px] font-bold font-mono text-right"
                    style={{ color: future ? '#ffffff12' : current ? c : past ? '#ffffff28' : '#ffffff12' }}
                  >
                    {lvl.fee.toFixed(1).replace(/\.0$/, '')} bps
                  </span>
                </div>
              )
            })}
          </div>
        </div>

        {/* RIGHT — info panel (30%) */}
        <div className="w-48 flex-shrink-0 border-l border-white/[0.06] flex flex-col">

          {/* Current tier + fee */}
          <div className="px-4 pt-5 pb-4 border-b border-white/[0.05] space-y-1.5">
            <p className="text-[11px] text-white/40">
              Tier: <span style={{ color: curColor }}>{cur.level}</span>
            </p>
            <p className="text-[11px] text-white/40">
              Fee: <span style={{ color: fee_is_overridden ? '#fbbf24' : curColor }}>
                {fmtStoredBpsPct(fee_bps)}
              </span>
              {fee_is_overridden && (
                <span className="ml-1 text-[9px] text-[#fbbf24]">set by admin.</span>
              )}
            </p>
          </div>

          {/* Progress */}
          <div className="px-4 py-4 border-b border-white/[0.05] space-y-2">
            <div className="flex justify-between text-[9px] font-mono text-white/20">
              <span>{fmtVol(cur.volume)}</span>
              {next && <span>{fmtVol(next.volume)}</span>}
            </div>
            <div className="h-1 rounded-full bg-white/[0.07] overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${pct}%`, background: curColor, boxShadow: `0 0 5px ${curColor}50` }}
              />
            </div>
            <p className="text-[11px] text-white/40">
              Progression: <span style={{ color: curColor }}>
                {pct === 0 ? '0' : pct >= 100 ? '100' : pct.toFixed(2).replace(/\.?0+$/, '')}%
              </span>
            </p>
          </div>

          {/* How it works */}
          <div className="px-4 py-4 flex-1">
            <p className="text-[10px] text-white/25 leading-relaxed">
              Every trade adds to your volume. Cross a threshold and your fee drops permanently.
            </p>
          </div>

        </div>
      </div>
    </div>
  )
}
