/**
 * ProfileModal — slide-in panel from right.
 * Click backdrop or × to close.
 */
import { useEffect, useState, useRef } from 'react'
import type { AppUser, ProfileData } from '../../types'
import { api } from '../../utils/api'
import { fmtStoredBps, fmtStoredBpsPct, fmtUsd, pnlColor } from '../../utils/format'
import { XPBar } from './XPBar'

interface Props {
  user: AppUser
  open: boolean
  onClose: () => void
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-widest font-semibold text-white/25">{label}</span>
      {children}
    </div>
  )
}

export function ProfileModal({ user, open, onClose }: Props) {
  const [profile, setProfile] = useState<ProfileData | null>(null)
  const [loading, setLoading] = useState(false)
  const [xpOpen, setXpOpen]   = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    api.getProfile()
      .then((d: any) => setProfile(d))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [open])

  // close on Escape
  useEffect(() => {
    if (!open) return
    const fn = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [open, onClose])

  const pnl  = profile ? Math.max(profile.bot_pnl, profile.wallet_pnl) : 0
  const dots = <span className="text-white/20 animate-pulse">···</span>

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px] transition-opacity duration-200 ${
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className={`fixed top-0 right-0 z-50 h-full w-full max-w-sm bg-[#0f1012] border-l border-white/[0.06]
          flex flex-col shadow-2xl transition-transform duration-300 ease-[cubic-bezier(.4,0,.2,1)]
          ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 h-14 border-b border-white/[0.05] flex-shrink-0">
          <span className="text-sm font-semibold text-white">{user.display_name || 'Profile'}</span>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-white/30 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 1l10 10M11 1L1 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-6">

          {/* Stats */}
          <div className="space-y-4">
            <Row label="Wallet">
              <span className="text-[11px] font-mono text-white/55 break-all leading-relaxed">
                {user.wallet_address || '—'}
              </span>
            </Row>

            <div className="grid grid-cols-2 gap-3">
              <div className="bg-white/[0.03] rounded-xl px-3 py-3 space-y-1">
                <span className="text-[9px] uppercase tracking-widest text-white/25 font-semibold">Total PnL</span>
                <p className={`text-base font-bold tabular-nums ${loading ? 'text-white/20' : pnlColor(pnl)}`}>
                  {loading ? dots : fmtUsd(pnl)}
                </p>
                <p className="text-[9px] text-white/18 leading-tight">Best of bot + wallet PnL</p>
              </div>
              <div className="bg-white/[0.03] rounded-xl px-3 py-3 space-y-1">
                <span className="text-[9px] uppercase tracking-widest text-white/25 font-semibold">Fee rate</span>
                <p className={`text-base font-bold tabular-nums ${loading ? 'text-white/20' : 'text-brand'}`}>
                  {loading ? dots : fmtStoredBps(profile?.fee_bps ?? 120)}
                </p>
                <p className="text-[9px] text-white/18 leading-tight">
                  {loading ? '' : fmtStoredBpsPct(profile?.fee_bps ?? 120)} · decreases with volume
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="bg-white/[0.03] rounded-xl px-3 py-3 space-y-1">
                <span className="text-[9px] uppercase tracking-widest text-white/25 font-semibold">Total volume</span>
                <p className={`text-sm font-bold tabular-nums font-mono ${loading ? 'text-white/20' : 'text-white/70'}`}>
                  {loading ? dots : fmtUsd(profile?.wallet_volume ?? 0, 0)}
                </p>
                <p className="text-[9px] text-white/18 leading-tight">All wallet activity</p>
              </div>
              <div className="bg-white/[0.03] rounded-xl px-3 py-3 space-y-1">
                <span className="text-[9px] uppercase tracking-widest text-white/25 font-semibold">Bot volume</span>
                <p className={`text-sm font-bold tabular-nums font-mono ${loading ? 'text-white/20' : 'text-white/70'}`}>
                  {loading ? dots : fmtUsd(profile?.trading_volume ?? 0, 0)}
                </p>
                <p className="text-[9px] text-white/18 leading-tight">Drives XP & fee level</p>
              </div>
            </div>
          </div>

          {/* Divider */}
          <div className="border-t border-white/[0.05]" />

          {/* Volume Reputation — collapsible */}
          <div className="space-y-3">
            <button
              className="w-full flex items-center justify-between group"
              onClick={() => setXpOpen(v => !v)}
            >
              <div className="flex items-center gap-2">
                <span className="text-[9px] uppercase tracking-widest font-semibold text-white/25 group-hover:text-white/40 transition-colors">
                  Volume Reputation
                </span>
                {!loading && (
                  <span
                    className="text-[9px] font-semibold px-1.5 py-0.5 rounded"
                    style={{
                      background: '#ffffff08',
                      color: '#ffffff35',
                    }}
                  >
                    Lv {(() => {
                      const { XP_LEVELS: lvls } = require('../../types')
                      let c = 0
                      for (let i = 0; i < lvls.length; i++) {
                        if ((profile?.trading_volume ?? 0) >= lvls[i].volume) c = i
                        else break
                      }
                      return c + 1
                    })()}
                  </span>
                )}
              </div>
              <svg
                width="10" height="10" viewBox="0 0 10 10" fill="none"
                className={`text-white/20 transition-transform duration-200 ${xpOpen ? 'rotate-180' : ''}`}
              >
                <path d="M2 3.5l3 3 3-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>

            <div
              className={`overflow-hidden transition-all duration-300 ease-[cubic-bezier(.4,0,.2,1)] ${
                xpOpen ? 'max-h-96 opacity-100' : 'max-h-0 opacity-0'
              }`}
            >
              {loading ? (
                <div className="space-y-2 py-1">
                  {Array.from({length:5}).map((_,i) => (
                    <div key={i} className="h-3 rounded bg-white/[0.04] animate-pulse" />
                  ))}
                </div>
              ) : (
                <XPBar trading_volume={profile?.trading_volume ?? 0} />
              )}
            </div>
          </div>

        </div>
      </div>
    </>
  )
}
