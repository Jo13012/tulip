import { useEffect, useState } from 'react'
import { api } from '../../utils/api'
import { fmtUsd } from '../../utils/format'
import type { ReferralListItem } from '../../types'

interface Props {
  onClose: () => void
}

function RewardIcon({ reward }: { reward: number }) {
  if (reward > 0) {
    return (
      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-emerald-500/15 flex-shrink-0">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 5l2.5 2.5L8 3" stroke="#10b981" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </span>
    )
  }

  return (
    <span className="flex items-center justify-center w-5 h-5 rounded-full bg-white/[0.06] flex-shrink-0">
      <div className="w-1.5 h-1.5 rounded-full bg-white/20" />
    </span>
  )
}

export function ReferralHistoryModal({ onClose }: Props) {
  const [referrals, setReferrals] = useState<ReferralListItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getReferrals()
      .then((data) => setReferrals(data ?? []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

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
        <button
          onClick={onClose}
          className="absolute top-3 right-3 z-20 w-7 h-7 flex items-center justify-center rounded-lg text-white/25 hover:text-white/60 hover:bg-white/[0.06] transition-colors"
        >
          <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
            <path d="M1 1l9 9M10 1L1 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </button>

        <div className="px-5 py-4 border-b border-white/[0.06]">
          <p className="text-sm font-bold text-white">Referrals</p>
        </div>

        <div className="grid grid-cols-[1fr_6.5rem_1.5rem] gap-x-4 px-5 py-2 border-b border-white/[0.04]">
          {['Username', 'Generated', ''].map((h, i) => (
            <span key={i} className={`text-[9px] uppercase tracking-widest text-white/20 font-semibold ${i === 1 ? 'text-right' : ''}`}>
              {h}
            </span>
          ))}
        </div>

        <div className="overflow-y-auto max-h-[420px]">
          {loading && (
            <div className="flex items-center justify-center py-10">
              <svg className="animate-spin w-5 h-5 text-white/30" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" strokeOpacity="0.2"/>
                <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </div>
          )}
          {!loading && referrals.length === 0 && (
            <p className="text-center text-white/20 text-xs py-10">No referrals yet.</p>
          )}
          {!loading && referrals.map((referral, i) => (
            <div
              key={referral.user_id}
              className={`grid grid-cols-[1fr_6.5rem_1.5rem] gap-x-4 items-center px-5 py-3 ${
                i < referrals.length - 1 ? 'border-b border-white/[0.04]' : ''
              } hover:bg-white/[0.02] transition-colors`}
            >
              <div className="min-w-0">
                <p className="text-[11px] text-white/75 font-mono truncate">{referral.username}</p>
              </div>

              <p className="text-[11px] text-white/80 tabular-nums text-right">
                {fmtUsd(referral.reward_usdc)}
              </p>

              <div className="flex justify-end">
                <RewardIcon reward={referral.reward_usdc} />
              </div>
            </div>
          ))}
          <div className="px-3 py-3 text-[10px] font-mono text-white/20 text-center leading-relaxed">
            Earn <span className="text-white/80">10%</span> of your referrals’ trading fees, 
            credited instantly in <span className="text-white/80 font-semibold">USDC</span> to your{" "}
            <span className="text-white/80">Arbitrum wallet</span>.{" "}
            <span className="text-white/20">
              We reserve the right to withhold rewards in cases of abuse, including self-referrals, multi-accounting, or other unfair practices.
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
