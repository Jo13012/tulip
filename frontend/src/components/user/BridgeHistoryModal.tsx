import { useEffect, useRef, useState } from 'react'
import { api } from '../../utils/api'

export interface BridgeTx {
  id: string
  amount_usdc: number
  state: string
  tx_hash: string | null
  source: string
  error_detail: string | null
  initiated_at: string
  completed_at: string | null
}

interface Props {
  onClose: () => void
}

function fmtDate(iso: string): string {
  const d = new Date(iso)
  const dd = String(d.getDate()).padStart(2, '0')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const yy = String(d.getFullYear()).slice(-2)
  const hh = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${dd}/${mm}/${yy} ${hh}:${min}`
}

function StatusIcon({ state }: { state: string }) {
  if (state === 'confirmed') {
    return (
      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-emerald-500/15 flex-shrink-0">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 5l2.5 2.5L8 3" stroke="#10b981" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </span>
    )
  }
  if (state === 'failed') {
    return (
      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-red-500/15 flex-shrink-0">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 2l6 6M8 2L2 8" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round"/>
        </svg>
      </span>
    )
  }
  return (
    <span className="flex items-center justify-center w-5 h-5 flex-shrink-0">
      <svg className="animate-spin" width="16" height="16" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="6" stroke="#facc15" strokeWidth="2" strokeOpacity="0.2"/>
        <path d="M8 2a6 6 0 0 1 6 6" stroke="#facc15" strokeWidth="2" strokeLinecap="round"/>
      </svg>
    </span>
  )
}

export function BridgeHistoryModal({ onClose }: Props) {
  const [txs, setTxs] = useState<BridgeTx[]>([])
  const [loading, setLoading] = useState(true)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function load() {
    api.getBridgeHistory()
      .then((data) => setTxs(data ?? []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  // Auto-refresh while any tx is pending
  useEffect(() => {
    const hasPending = txs.some(tx => tx.state === 'initiated' || tx.state === 'submitted')
    if (hasPending) {
      intervalRef.current = setInterval(load, 3000)
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [txs])

  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

      <div
        className="relative z-10 w-full max-w-lg bg-[#111214] border border-white/[0.08] rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Close */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 z-20 w-7 h-7 flex items-center justify-center rounded-lg text-white/25 hover:text-white/60 hover:bg-white/[0.06] transition-colors"
        >
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
            <path d="M1 1l9 9M10 1L1 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </button>

        {/* Header */}
        <div className="px-5 py-4 border-b border-white/[0.06]">
          <p className="text-sm font-bold text-white">Bridge History</p>
        </div>

        {/* Column headers */}
        <div className="grid grid-cols-[6rem_1fr_5rem_1.5rem] gap-x-4 px-5 py-2 border-b border-white/[0.04]">
          {['Date', 'Tx ID', 'Amount', ''].map((h, i) => (
            <span key={i} className={`text-[9px] uppercase tracking-widest text-white/20 font-semibold ${i === 2 ? 'text-right' : ''}`}>
              {h}
            </span>
          ))}
        </div>

        {/* Rows */}
        <div className="overflow-y-auto max-h-[420px]">
          {loading && (
            <div className="flex items-center justify-center py-10">
              <svg className="animate-spin w-5 h-5 text-white/30" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" strokeOpacity="0.2"/>
                <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </div>
          )}
          {!loading && txs.length === 0 && (
            <p className="text-center text-white/20 text-xs py-10">No bridge transactions yet.</p>
          )}
          {!loading && txs.map((tx, i) => (
            <div
              key={tx.id}
              className={`grid grid-cols-[6rem_1fr_5rem_1.5rem] gap-x-4 items-center px-5 py-3 ${
                i < txs.length - 1 ? 'border-b border-white/[0.04]' : ''
              } hover:bg-white/[0.02] transition-colors`}
            >
              {/* Date */}
              <p className="text-[11px] text-white/50 tabular-nums">{fmtDate(tx.initiated_at)}</p>

              {/* Tx ID */}
              <div className="min-w-0">
                {tx.tx_hash ? (
                  <a
                    href={`https://arbiscan.io/tx/${tx.tx_hash}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] text-white/55 hover:text-white/80 transition-colors underline underline-offset-2 font-mono"
                  >
                    {tx.tx_hash.slice(0, 8)}…{tx.tx_hash.slice(-6)}
                  </a>
                ) : (
                  <span className="text-[11px] text-white/15 font-mono">—</span>
                )}
              </div>

              {/* Amount */}
              <p className="text-[11px] text-white/80 tabular-nums text-right">
                {tx.amount_usdc > 0 ? `$${tx.amount_usdc.toFixed(2)}` : '—'}
              </p>

              {/* Status */}
              <div className="flex justify-end">
                <StatusIcon state={tx.state} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
