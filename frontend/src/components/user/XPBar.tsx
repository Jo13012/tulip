import { XP_LEVELS } from '../../types'

interface Props { trading_volume: number }

const COLORS = ['#60a5fa','#38bdf8','#34d399','#a3e635','#facc15','#fb923c','#f87171','#c084fc','#f472b6','#e879f9']

function fmtVol(v: number) {
  if (v >= 1_000_000) return `$${(v/1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `$${(v/1_000).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

export function XPBar({ trading_volume }: Props) {
  let cur = 0
  for (let i = 0; i < XP_LEVELS.length; i++) {
    if (trading_volume >= XP_LEVELS[i].volume) cur = i
    else break
  }
  const next = XP_LEVELS[cur + 1] ?? null
  const pct  = next
    ? Math.min(100, ((trading_volume - XP_LEVELS[cur].volume) / (next.volume - XP_LEVELS[cur].volume)) * 100)
    : 100

  return (
    <div className="space-y-[7px]">
      {XP_LEVELS.map((lvl, i) => {
        const past    = i < cur
        const current = i === cur
        const future  = i > cur
        const c       = COLORS[i]
        const w       = past ? 100 : current ? pct : 0

        return (
          <div key={i} className="flex items-center gap-2.5">
            {/* level dot */}
            <span
              className="text-[10px] font-semibold tabular-nums w-4 text-right leading-none flex-shrink-0"
              style={{ color: future ? '#ffffff14' : current ? c : '#ffffff30' }}
            >
              {lvl.level}
            </span>

            {/* bar track — each level uses its own color */}
            <div className="flex-1 h-[3px] rounded-full bg-white/[0.06]">
              {!future && (
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${w}%`,
                    background: c,
                    boxShadow: current ? `0 0 6px ${c}60` : 'none',
                    transition: 'width 0.5s cubic-bezier(.4,0,.2,1)',
                  }}
                />
              )}
            </div>

            {/* threshold */}
            <span
              className="text-[9px] font-mono w-9 text-right flex-shrink-0 tabular-nums"
              style={{ color: future ? '#ffffff0e' : '#ffffff22' }}
            >
              {fmtVol(lvl.volume)}
            </span>

            {/* fee */}
            <span
              className="text-[9px] font-mono font-bold w-8 text-right flex-shrink-0"
              style={{ color: future ? '#ffffff0e' : current ? c : past ? '#ffffff20' : '#ffffff0e' }}
            >
              {lvl.fee}%
            </span>
          </div>
        )
      })}

      <p className="pt-1 text-[9px] text-white/15 font-mono">
        {fmtVol(trading_volume)} traded
        {next && <span className="opacity-60"> · {fmtVol(next.volume - trading_volume)} to lv {cur + 2}</span>}
      </p>
    </div>
  )
}
