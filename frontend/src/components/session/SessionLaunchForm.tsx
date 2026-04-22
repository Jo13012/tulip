import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { api } from '../../utils/api'
import { useAppStore } from '../../store'
import type { Strategy, ViabilityResult } from '../../types'
import { InfoTip } from '../ui/Tooltip'
import { fmtUsd } from '../../utils/format'

interface Props {
  strategy: Strategy
  walletId: string
  onLaunched: () => void
}

export function SessionLaunchForm({ strategy, walletId, onLaunched }: Props) {
  const addToast = useAppStore((s) => s.addToast)

  const [grossCapital, setGrossCapital] = useState('')
  const [stopMode, setStopMode]     = useState<'days' | 'volume'>('days')
  const [stopDays, setStopDays]     = useState('7')
  const [targetVol, setTargetVol]   = useState('')
  const [slPct, setSlPct]           = useState('20')
  const [viability, setViability]   = useState<ViabilityResult | null>(null)
  const [checking, setChecking]     = useState(false)
  const [launching, setLaunching]   = useState(false)

  const gross      = parseFloat(grossCapital) || 0
  const reservePct = strategy.reserve_pct
  const feeRes     = gross * 0.95
  const deployable = gross - feeRes

  useEffect(() => {
    if (!gross || gross <= 0) { setViability(null); return }
    const t = setTimeout(async () => {
      setChecking(true)
      try {
        const result: any = await api.checkViability(gross)
        setViability(result)
      } catch { setViability(null) }
      finally { setChecking(false) }
    }, 500)
    return () => clearTimeout(t)
  }, [gross])

  async function handleLaunch() {
    if (!viability?.viable) return
    setLaunching(true)
    try {
      await api.launchSession({
        wallet_id:      walletId,
        gross_capital:  gross * 0.97,
        stop_mode:      stopMode,
        stop_days:      stopMode === 'days' ? parseInt(stopDays) : null,
        target_volume:  stopMode === 'volume' ? parseFloat(targetVol) : null,
        session_sl_pct: parseFloat(slPct),
      })
      addToast({ type: 'success', message: 'Session launched successfully.' })
      onLaunched()
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally { setLaunching(false) }
  }

  const canLaunch = viability?.viable && !launching && gross > 0

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="card space-y-6">
      <h2 className="text-lg font-semibold">Launch Session</h2>

      <div className="space-y-3">
        <div>
          <label className="label flex items-center gap-1 mb-1.5">
            Total Capital (USDC)
            <InfoTip content="The total USDC you want to deploy for this session, including fee reserve." />
          </label>
          <input className="input" type="number" min="0" placeholder="e.g. 1000"
            value={grossCapital} onChange={(e) => setGrossCapital(e.target.value)} />
        </div>

        {gross > 0 && (
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div className="bg-white/5 rounded-xl px-3 py-2">
              <div className="flex items-center gap-1 label mb-1">
                Deployable
                <InfoTip content="Capital actively used for orders. Gross minus fee reserve." />
              </div>
              <div className="font-semibold">{fmtUsd(deployable)}</div>
            </div>
            <div className="bg-white/5 rounded-xl px-3 py-2">
              <div className="flex items-center gap-1 label mb-1">
                Fee Reserve ({reservePct}%)
                <InfoTip content="Reserved to cover platform fees on executed trades. Never used for orders." />
              </div>
              <div className="font-semibold text-white/60">{fmtUsd(feeRes)}</div>
            </div>
          </div>
        )}
      </div>

      {gross > 0 && (
        <div className={`rounded-xl px-4 py-3 text-sm border ${
          checking          ? 'border-white/10 bg-white/3 text-white/40' :
          viability?.viable ? 'border-success/20 bg-success/5 text-success' :
                              'border-danger/20 bg-danger/5 text-danger'
        }`}>
          {checking ? (
            <span>Checking viability…</span>
          ) : viability ? (
            <div className="space-y-1">
              <div className="font-semibold">
                {viability.viable ? '✓ Capital is sufficient' : '✕ Insufficient capital'}
              </div>
              <div className="text-white/50 text-xs">
                Minimum: {fmtUsd(viability.min_capital)} · Recommended: {fmtUsd(viability.recommended_capital)}
              </div>
              {!viability.viable && viability.reason && (
                <div className="text-xs">{viability.reason}</div>
              )}
            </div>
          ) : null}
        </div>
      )}

      <div className="space-y-3">
        <label className="label">Stop Mode</label>
        <div className="flex gap-2">
          {(['days', 'volume'] as const).map((mode) => (
            <button key={mode} onClick={() => setStopMode(mode)}
              className={`flex-1 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                stopMode === mode ? 'bg-brand text-white' : 'bg-white/5 text-white/50 hover:bg-white/10'
              }`}
            >
              {mode === 'days' ? 'Duration (days)' : 'Volume target'}
            </button>
          ))}
        </div>
        {stopMode === 'days' ? (
          <div>
            <label className="label mb-1.5 flex items-center gap-1">
              Duration (days)
              <InfoTip content="Session stops accepting new entries after this many days." />
            </label>
            <input className="input" type="number" min="1" value={stopDays}
              onChange={(e) => setStopDays(e.target.value)} />
          </div>
        ) : (
          <div>
            <label className="label mb-1.5 flex items-center gap-1">
              Target Volume (USDC)
              <InfoTip content="Session stops accepting new entries after reaching this notional volume." />
            </label>
            <input className="input" type="number" min="0" placeholder="e.g. 50000" value={targetVol}
              onChange={(e) => setTargetVol(e.target.value)} />
          </div>
        )}
      </div>

      <div>
        <label className="label mb-1.5 flex items-center gap-1">
          Session Stop-Loss (%)
          <InfoTip content={
            <span>
              If total loss (paid fees + unrealized losses) reaches this % of your gross capital,
              the session will hard stop and unwind all positions immediately.
            </span>
          } />
        </label>
        <input className="input" type="number" min="1" max="100" value={slPct}
          onChange={(e) => setSlPct(e.target.value)} />
      </div>

      <button className="btn-primary w-full text-base py-3" disabled={!canLaunch} onClick={handleLaunch}>
        {launching ? 'Launching…' : 'Launch Session'}
      </button>
    </motion.div>
  )
}
