import { useState } from 'react'

interface PatchEntry {
  version: string
  date: string
  notes: string[]
}

// Add new patches at the top. Banner shows for the latest version until dismissed.
export const CHANGELOG: PatchEntry[] = [
  {
    version: '1.3.1',
    date: 'May 20, 2025',
    notes: [
      'Builder fees cut in half — from 0.40% to 0.20% per trade. Same strategy, better returns. No action needed on your end.',
      'Session recovery is now smarter. If the engine restarts mid-session, all fills that happened while it was offline are recovered and counted toward your volume and PnL. Nothing gets lost.',
      'Reliability improvements across the board — faster reconnections, cleaner state after restarts, and fewer edge cases that could cause a session to stall.',
    ],
  },
  {
    version: '1.3.0',
    date: 'May 11, 2025',
    notes: [
      'Deposit flow completely reworked. No more Arbitrum wallet, no more bridge. You now send directly to your Polymarket account — USDC, USDC.e, ETH or SOL on Arbitrum, Base or Solana. One address, ~2 min, done.',
      'Fees are now deducted directly from your Polymarket balance instead of a separate ETH/USDC reserve on Arbitrum. Much simpler — your whole balance is your capital.',
      'Referral system is temporarily paused. We want to find a fair way to reward referrers before turning it back on. If you have ideas, come share them on Discord — we\'re listening.',
      'Launch form now reads your actual available balance (balance minus open positions). If it\'s below the minimum, you\'ll see exactly why and what to do.',
      'Withdraw tab now has a clear step-by-step guide to export your key and withdraw from Polymarket directly.',
    ],
  },
]

const LATEST = CHANGELOG[0]

interface Props { userId: string }

export function ChangelogBanner({ userId }: Props) {
  const storageKey = `changelog_dismissed_${LATEST.version}_${userId}`
  const [visible, setVisible] = useState(() => !localStorage.getItem(storageKey))
  const [modalOpen, setModalOpen] = useState(false)

  function dismiss() {
    localStorage.setItem(storageKey, '1')
    setVisible(false)
    setModalOpen(false)
  }

  if (!visible) return null

  return (
    <>
      {/* Banner — full width, single line, no padding wrapper */}
      <div className="w-full flex items-center gap-3 px-5 sm:px-8 py-2 border-b border-white/[0.05] bg-white/[0.015] animate-[fadeIn_200ms_ease-out]">
        {/* Left: tag + version + date */}
        <span className="flex-shrink-0 text-[9px] font-bold uppercase tracking-widest text-brand/60">New</span>
        <span className="flex-shrink-0 text-[9px] text-white/20">·</span>
        <span className="flex-shrink-0 text-[10px] font-semibold text-white/50">v{LATEST.version}</span>
        <span className="flex-shrink-0 text-[9px] text-white/20 hidden sm:inline">·</span>
        <span className="flex-shrink-0 text-[10px] text-white/30 hidden sm:inline">{LATEST.date}</span>
        <span className="flex-shrink-0 text-[9px] text-white/15 hidden md:inline">·</span>
        {/* First note — truncated on small screens */}
        <span className="flex-1 text-[10px] text-white/35 truncate hidden md:block min-w-0">
          {LATEST.notes[0]}
        </span>
        {/* Actions */}
        <div className="flex items-center gap-3 flex-shrink-0 ml-auto">
          <button
            onClick={() => setModalOpen(true)}
            className="text-[10px] font-semibold text-brand/55 hover:text-brand/90 transition-colors"
          >
            Read more
          </button>
          <button
            onClick={dismiss}
            className="text-[13px] leading-none text-white/20 hover:text-white/50 transition-colors"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      </div>

      {/* Modal */}
      {modalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-[fadeIn_120ms_ease-out]"
          onClick={() => setModalOpen(false)}
        >
          <div
            className="relative w-full max-w-md rounded-2xl border border-white/[0.08] bg-[#0f1012] shadow-2xl overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 pt-5 pb-4 border-b border-white/[0.06]">
              <div>
                <p className="text-[13px] font-semibold text-white/80">What's new</p>
                <p className="text-[10px] text-white/30 mt-0.5">Latest platform updates</p>
              </div>
              <button onClick={() => setModalOpen(false)} className="text-white/25 hover:text-white/60 transition-colors text-[18px] leading-none">×</button>
            </div>

            <div className="overflow-y-auto max-h-[65vh] divide-y divide-white/[0.04]">
              {CHANGELOG.map(patch => (
                <div key={patch.version} className="px-5 py-4">
                  <div className="flex items-baseline gap-2.5 mb-3">
                    <span className="text-[11px] font-bold text-white/70">v{patch.version}</span>
                    <span className="text-[10px] text-white/25">{patch.date}</span>
                  </div>
                  <ul className="space-y-2.5">
                    {patch.notes.map((note, i) => (
                      <li key={i} className="flex gap-2.5 items-start">
                        <span className="mt-[5px] w-1 h-1 rounded-full bg-white/18 flex-shrink-0" />
                        <span className="text-[11px] text-white/50 leading-relaxed">{note}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>

            <div className="px-5 py-4 border-t border-white/[0.06]">
              <button
                onClick={dismiss}
                className="w-full py-2.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-[11px] font-semibold text-white/40 hover:text-white/70 transition-all"
              >
                Got it — don't show again
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
