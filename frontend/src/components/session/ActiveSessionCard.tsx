import { useState } from 'react'
import { motion } from 'framer-motion'
import type { Session } from '../../types'
import { StatusBadge } from '../ui/StatusBadge'
import { MetricCard } from '../ui/MetricCard'
import { ConfirmModal } from '../ui/ConfirmModal'
import { InfoTip } from '../ui/Tooltip'
import { api } from '../../utils/api'
import { useAppStore } from '../../store'
import { fmtUsd, fmtDuration, pnlColor } from '../../utils/format'
import { useSessionWebSocket } from '../../hooks/useWebSocket'

interface Props {
  session: Session
  onStopped: () => void
}

function pct(value: number, total: number) {
  if (!total) return 0
  return Math.min(100, Math.max(0, (value / total) * 100))
}

export function ActiveSessionCard({ session, onStopped }: Props) {
  const addToast = useAppStore((s) => s.addToast)
  const [stopModal, setStopModal] = useState<'soft' | 'hard' | null>(null)
  const [stopping, setStopping]   = useState(false)

  useSessionWebSocket(session.session_id)

  const rt     = session.runtime
  const health = session.health_state

  async function handleStop(mode: 'soft' | 'hard') {
    setStopping(true)
    setStopModal(null)
    try {
      await api.stopSession(session.session_id, mode)
      addToast({
        type: 'info',
        message: mode === 'soft'
          ? 'Soft stop initiated — waiting for positions to exit.'
          : 'Hard stop executed — all positions unwound.',
      })
      onStopped()
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally { setStopping(false) }
  }

  // Progress calculation
  const progressPct = (() => {
    if (!rt) return 0
    if (session.stop_mode === 'volume' && session.target_volume) {
      return pct(rt.total_volume, session.target_volume)
    }
    if (session.stop_mode === 'days' && session.stop_days && session.started_at) {
      const elapsed = (Date.now() - new Date(session.started_at).getTime()) / 1000
      return pct(elapsed, session.stop_days * 86400)
    }
    return 0
  })()

  const grossPnl = rt ? rt.realized_pnl + rt.unrealized_pnl : 0
  const netPnl   = grossPnl - (rt?.paid_fees ?? 0)
  const canStop   = !['stopped', 'stopping', 'error'].includes(session.status)

  return (
    <>
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="card space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="text-base font-semibold mb-1">Active Session</div>
            <StatusBadge status={health} />
            {health === 'recovering' && (
              <div className="text-xs text-warning mt-1">Reconnecting to exchange…</div>
            )}
          </div>
          <div className="text-right text-xs text-white/30">
            {rt && <div>Uptime {fmtDuration(rt.uptime_s)}</div>}
          </div>
        </div>

        {/* Progress bar */}
        {session.stop_mode && (
          <div>
            <div className="flex justify-between mb-1 text-[11px]">
              <span className="text-white/40">
                Progress
                <span className="text-white/20 ml-1">
                  ({session.stop_mode === 'days'
                    ? `${session.stop_days}d duration`
                    : `${fmtUsd(session.target_volume ?? 0)} volume target`})
                </span>
              </span>
              <span className="text-white/60 font-mono">{progressPct.toFixed(1)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
              <div
                className="h-full rounded-full bg-brand transition-all duration-700"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>
        )}

        {/* Primary session metrics */}
        <div className="grid grid-cols-2 gap-2">
          <MetricCard
            label="Volume"
            value={fmtUsd(rt?.total_volume ?? 0)}
            tooltip="Total notional traded (buys + sells)."
          />
          <MetricCard
            label="Trades"
            value={String(rt?.trade_count ?? 0)}
            tooltip="Completed round-trip trades (filled buy + filled sell)."
          />
          <MetricCard
            label="Gross PnL"
            value={<span className={pnlColor(grossPnl)}>{fmtUsd(grossPnl)}</span>}
            sub={`R ${fmtUsd(rt?.realized_pnl ?? 0)} · U ${fmtUsd(rt?.unrealized_pnl ?? 0)}`}
            tooltip="PnL before deducting platform fees."
            accent={grossPnl >= 0 ? 'success' : 'danger'}
          />
          <MetricCard
            label="Fees"
            value={fmtUsd(rt?.paid_fees ?? 0)}
            tooltip="Platform fees charged on executed fills."
          />
          <MetricCard
            label="Net PnL"
            value={<span className={pnlColor(netPnl)}>{fmtUsd(netPnl)}</span>}
            tooltip="Gross PnL minus fees paid."
            accent={netPnl >= 0 ? 'success' : 'danger'}
          />
          <MetricCard
            label="Deployed"
            value={fmtUsd(rt?.total_deployed ?? 0)}
            tooltip="USDC currently locked in open lots."
          />
        </div>

        {/* Stop controls */}
        {canStop && (
          <div className="flex gap-3 pt-2 border-t border-white/5">
            <button className="btn-ghost flex-1 text-sm" onClick={() => setStopModal('soft')} disabled={stopping}>
              <span className="flex items-center justify-center gap-2">
                Soft Stop
                <InfoTip content="Stop new entries. Current positions exit cleanly at target prices." />
              </span>
            </button>
            <button className="btn-danger flex-1 text-sm" onClick={() => setStopModal('hard')} disabled={stopping}>
              <span className="flex items-center justify-center gap-2">
                Hard Stop
                <InfoTip content="Cancel all orders and taker-sell all positions immediately at market price." />
              </span>
            </button>
          </div>
        )}
      </motion.div>

      <ConfirmModal
        open={stopModal === 'soft'}
        title="Soft Stop"
        description={
          <span>No new entries will be placed. Existing positions will exit at their target prices.
          This may take some time if markets are quiet.</span>
        }
        confirmLabel="Soft Stop"
        onConfirm={() => handleStop('soft')}
        onCancel={() => setStopModal(null)}
      />
      <ConfirmModal
        open={stopModal === 'hard'}
        title="Hard Stop"
        description={
          <span>All pending orders will be cancelled and all open positions will be sold at market
          price immediately. This may result in losses if prices are unfavorable.</span>
        }
        confirmLabel="Hard Stop"
        variant="danger"
        onConfirm={() => handleStop('hard')}
        onCancel={() => setStopModal(null)}
      />
    </>
  )
}
