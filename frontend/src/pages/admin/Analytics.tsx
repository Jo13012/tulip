import { useEffect, useState } from 'react'
import { api } from '../../utils/api'
import { fmtUsd, fmtMul, pnlColor } from '../../utils/format'

function SummaryCard({ label, value, accent = 'text-white/70' }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="bg-[#0d0e10] border border-white/[0.05] rounded-xl px-4 py-3">
      <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-1.5">{label}</div>
      <div className={`text-[18px] font-bold font-mono tabular-nums leading-none ${accent}`}>{value}</div>
    </div>
  )
}

export default function AdminAnalytics() {
  const [data, setData]       = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.admin.globalStrategyAnalytics()
      .then((d: any) => setData(d))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="p-6 space-y-3">
        {[1,2,3].map(i => <div key={i} className="h-16 bg-white/[0.02] rounded-xl animate-pulse" />)}
      </div>
    )
  }

  if (!data?.summary) {
    return (
      <div className="p-6">
        <h1 className="text-sm font-bold text-white/80 uppercase tracking-widest mb-6">Analytics</h1>
        <div className="border border-white/[0.05] rounded-xl px-5 py-10 text-center">
          <div className="text-white/20 text-xs font-mono uppercase tracking-widest">No data yet</div>
          <div className="text-white/12 text-[10px] mt-1.5">Analytics appear after sessions run.</div>
        </div>
      </div>
    )
  }

  const { summary: strat, versions, markets } = data

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-sm font-bold text-white/80 uppercase tracking-widest">Analytics</h1>

      {/* Summary */}
      <div>
        <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-2">Global Summary</div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          <SummaryCard label="Total Sessions"  value={strat.total_sessions} />
          <SummaryCard label="Active Now"       value={strat.active_sessions} accent={strat.active_sessions > 0 ? 'text-emerald-400' : 'text-white/30'} />
          <SummaryCard label="Volume Multiple"  value={fmtMul(strat.volume_multiple)} accent="text-brand" />
          <SummaryCard label="Total Volume"     value={fmtUsd(strat.total_volume)} />
          <SummaryCard label="Total PnL"        value={fmtUsd(strat.total_pnl)} accent={pnlColor(strat.total_pnl)} />
          <SummaryCard label="Total Fees"       value={fmtUsd(strat.total_fees)} accent="text-white/35" />
        </div>
      </div>

      {/* Version performance */}
      {versions?.length > 0 && (
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-2">Version Performance</div>
          <div className="border border-white/[0.05] rounded-xl overflow-hidden overflow-x-auto">
            <div className="grid grid-cols-5 gap-2 px-4 py-2 bg-white/[0.02] border-b border-white/[0.05] min-w-[400px]">
              {['Version', 'Sessions', 'Volume', 'PnL', 'Fees'].map(h => (
                <div key={h} className="text-[9px] font-semibold uppercase tracking-widest text-white/20">{h}</div>
              ))}
            </div>
            {versions.map((v: any) => (
              <div key={v.version_id} className="grid grid-cols-5 gap-2 px-4 py-3 border-b border-white/[0.03] last:border-0 hover:bg-white/[0.02] transition-colors min-w-[400px]">
                <div className="text-[11px] font-mono text-brand">{v.version_label}</div>
                <div className="text-[11px] font-mono text-white/50">{v.session_count}</div>
                <div className="text-[11px] font-mono text-white/50 tabular-nums">{fmtUsd(v.total_volume, 0)}</div>
                <div className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(v.total_pnl)}`}>{fmtUsd(v.total_pnl)}</div>
                <div className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(v.total_fees)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Market performance */}
      {markets?.length > 0 && (
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-2">Market Performance</div>
          <div className="border border-white/[0.05] rounded-xl overflow-hidden overflow-x-auto">
            <div className="grid grid-cols-5 gap-2 px-4 py-2 bg-white/[0.02] border-b border-white/[0.05] min-w-[480px]">
              {['Market', 'Exposures', 'Volume', 'PnL', 'Fees'].map(h => (
                <div key={h} className="text-[9px] font-semibold uppercase tracking-widest text-white/20">{h}</div>
              ))}
            </div>
            {markets.slice(0, 30).map((m: any) => (
              <div key={m.polymarket_url} className="grid grid-cols-5 gap-2 px-4 py-3 border-b border-white/[0.03] last:border-0 hover:bg-white/[0.02] transition-colors min-w-[480px]">
                <div className="text-[11px] text-white/55 truncate">{m.question || m.polymarket_url}</div>
                <div className="text-[11px] font-mono text-white/40">{m.exposure_count}</div>
                <div className="text-[11px] font-mono text-white/50 tabular-nums">{fmtUsd(m.volume, 0)}</div>
                <div className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(m.realized_pnl)}`}>{fmtUsd(m.realized_pnl)}</div>
                <div className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(m.fees_paid)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
