import { motion } from 'framer-motion'
import type { Strategy } from '../../types'
import { InfoTip } from '../ui/Tooltip'
import { MetricCard } from '../ui/MetricCard'
import { fmtPct, fmtMul } from '../../utils/format'

interface Props { strategy: Strategy }

export function StrategyCard({ strategy }: Props) {
  const est = strategy.estimate

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="card space-y-4"
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-widest text-white/30 mb-1">Current Version</div>
          <div className="text-2xl font-bold text-brand">{strategy.version_label ?? '—'}</div>
        </div>
        <div className={`text-[11px] px-2.5 py-1 rounded-full font-medium flex-shrink-0 ml-4 ${
          strategy.available
            ? 'bg-green-500/15 text-green-400'
            : strategy.global_pause
              ? 'bg-orange-500/20 text-orange-400'
              : 'bg-white/10 text-white/40'
        }`}>
          {strategy.available ? 'Launch Available' : strategy.unavailable_reason ?? 'Unavailable'}
        </div>
      </div>

      {!strategy.available && strategy.unavailable_reason && (
        <div className="flex items-center gap-2 bg-warning/8 border border-warning/15 rounded-xl px-4 py-3 text-sm text-warning/80">
          <span>⚠</span>
          <span>{strategy.unavailable_reason} — sessions cannot be launched right now.</span>
        </div>
      )}

      <div className="flex items-center gap-2 text-sm text-white/50">
        <span>{strategy.reserve_pct}% of capital held as fee reserve.</span>
        <InfoTip content={
          <span>
            The fee reserve covers platform fees on executed trades.
            Separate from deployable capital, never used for orders.
          </span>
        } />
      </div>

      <div>
        <div className="flex items-center gap-1 mb-3">
          <span className="label">Historical Estimate</span>
          <InfoTip content={
            <span>
              Averages from closed sessions. Past performance is not indicative
              of future results. Shown only when sufficient data exists.
            </span>
          } />
        </div>
        {est ? (
          <div className="grid grid-cols-2 gap-3">
            <MetricCard
              label="Avg Daily Volume Multiple"
              value={fmtMul(est.avg_daily_volume_multiple)}
              tooltip="Average daily notional volume relative to deployed capital."
            />
            <MetricCard
              label="Avg PnL"
              value={fmtPct(est.avg_pnl_pct)}
              accent={est.avg_pnl_pct >= 0 ? 'success' : 'danger'}
              tooltip="Average realized PnL as % of deployed capital."
            />
            <div className="col-span-2 text-xs text-white/25 italic">
              {est.disclaimer} ({est.sample_size} sessions)
            </div>
          </div>
        ) : (
          <div className="text-sm text-white/30 italic">
            Estimates appear after enough sessions complete.
          </div>
        )}
      </div>
    </motion.div>
  )
}
