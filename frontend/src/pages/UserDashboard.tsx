/**
 * UserDashboard - User shell.
 * Layout: fixed top bar + tab navigation + content area.
 * Tabs: Run | History | Performance | Profile | Settings
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles } from 'lucide-react'

import { useAppStore } from '../store'
import { api } from '../utils/api'
import type { AppUser } from '../types'
import tulipLogo from '../assets/tulip.png'
import { FUNDING_STATUS_REFRESH_MS } from '../utils/bridge'

import { RunTab } from '../components/user/RunTab'
import { HistoryTab } from '../components/user/HistoryTab'
import { PerformanceTab } from '../components/user/PerformanceTab'
import { SettingsTab } from '../components/user/SettingsTab'
import { ProfileTab } from '../components/user/ProfileTab'
import { FaqTab } from '../components/user/FaqTab'

type Tab = 'run' | 'history' | 'performance' | 'profile' | 'settings' | 'faq'

interface Props {
  user: AppUser
  onSignOut: () => void
}

function UserMenu({ user, usdcBalance, feeUsdc, feeEth, onSignOut }: {
  user: AppUser
  usdcBalance: number | null
  feeUsdc: number | null
  feeEth: number | null
  onSignOut: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const fmt2 = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold transition-colors ${
          open ? 'bg-white/[0.08] text-white/80' : 'text-white/45 hover:text-white/70 hover:bg-white/[0.05]'
        }`}
      >
        <span>{user.display_name?.trim() || '—'}</span>
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className={`transition-transform duration-150 ${open ? 'rotate-180' : ''}`}>
          <path d="M2 3.5l3 3 3-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 w-64 rounded-xl border border-white/[0.08] bg-[#0f1012] shadow-2xl z-50 overflow-hidden animate-[fadeIn_120ms_ease-out]">
          {/* Balances */}
          <div className="px-4 py-3 space-y-2.5 border-b border-white/[0.06]">
            {usdcBalance != null && (
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-white/30 uppercase tracking-widest font-semibold">Trading</span>
                <span className="text-[12px] font-mono font-semibold text-white/70">${fmt2(usdcBalance)}</span>
              </div>
            )}
            {feeUsdc != null && (
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-white/30 uppercase tracking-widest font-semibold">Wallet USDC</span>
                <span className="text-[12px] font-mono font-semibold text-white/70">${fmt2(feeUsdc)}</span>
              </div>
            )}
            {feeEth != null && (
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-white/30 uppercase tracking-widest font-semibold">Wallet ETH</span>
                <span className="text-[12px] font-mono font-semibold text-white/50">{feeEth.toFixed(5)}</span>
              </div>
            )}
            {usdcBalance == null && feeUsdc == null && feeEth == null && (
              <span className="text-[11px] text-white/20">Loading balances…</span>
            )}
          </div>

          {/* Sign out */}
          <div className="px-2 py-1.5">
            <button
              onClick={() => { setOpen(false); onSignOut() }}
              className="w-full text-left px-3 py-2 rounded-lg text-[11px] text-white/40 hover:text-white/70 hover:bg-white/[0.05] transition-colors"
            >
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function UserDashboard({ user, onSignOut }: Props) {
  const { strategy, setStrategy, activeSession, setActiveSession, setFundingStatus } = useAppStore()
  const fundingStatus = useAppStore(s => s.fundingStatus)
  const [tab, setTab] = useState<Tab>('run')
  const [loading, setLoading] = useState(true)
  const [usdcBalance, setUsdcBalance] = useState<number | null>(null)
  const [showLowBalance, setShowLowBalance] = useState(false)
  const [feeUsdc, setFeeUsdc] = useState<number | null>(null)
  const [feeEth, setFeeEth] = useState<number | null>(null)
  const [needsUsdc, setNeedsUsdc] = useState(false)
  const [needsEth, setNeedsEth] = useState(false)
  const [warnUsdc, setWarnUsdc] = useState(false)
  const [warnEth, setWarnEth] = useState(false)
  const [warnDismissed, setWarnDismissed] = useState(false)
  const [fundingMessage, setFundingMessage] = useState('')

  async function refreshFundingStatus() {
    let status: any
    try {
      status = await api.getFundingStatus()
    } catch (err: any) {
      if (err?.message === 'UNAUTHORIZED') { onSignOut(); return }
      throw err
    }
    setFundingStatus(status)
    setFeeUsdc(status.usdc_balance ?? null)
    setFeeEth(status.eth_balance ?? null)
    setUsdcBalance(status.trading_balance ?? null)
    setNeedsUsdc(Boolean(status.needs_usdc))
    setNeedsEth(Boolean(status.needs_eth))
    setWarnUsdc(Boolean(status.warn_usdc))
    setWarnEth(Boolean(status.warn_eth))
    setFundingMessage(status.message || '')
  }

  useEffect(() => {
    setLoading(true)
    const loadData = async (attempt = 0) => {
      try {
        const [strategy, session] = await Promise.all([
          api.getStrategy() as Promise<any>,
          api.getActiveSession() as Promise<any>,
        ])
        setStrategy(strategy)
        setActiveSession(session)
        refreshFundingStatus().catch(() => {})
      } catch (err: any) {
        // Privy token not yet ready after F5 — retry up to 3 times with backoff
        if (err?.message === 'UNAUTHORIZED' && attempt < 3) {
          setTimeout(() => loadData(attempt + 1), 800 * (attempt + 1))
          return
        }
        // Auth lost permanently — kick back to login
        if (err?.message === 'UNAUTHORIZED') {
          onSignOut()
          return
        }
        console.error('Error loading data:', err)
      } finally {
        if (attempt === 0) setLoading(false)
      }
    }
    loadData()
  }, [])

  useEffect(() => {
    const isFundingCritical = !fundingStatus?.onboarding_complete
      || needsUsdc || needsEth || warnUsdc || warnEth
    const intervalMs = isFundingCritical ? FUNDING_STATUS_REFRESH_MS : 30000
    const interval = setInterval(() => {
      refreshFundingStatus().catch(() => {})
    }, intervalMs)
    return () => clearInterval(interval)
  }, [needsUsdc, needsEth, warnUsdc, warnEth, fundingStatus?.onboarding_complete])

  useEffect(() => {
    if (usdcBalance == null) return
    const key = `low_balance_banner_dismissed_${user.user_id}`
    if (usdcBalance < 1) {
      const dismissed = localStorage.getItem(key)
      setShowLowBalance(!dismissed)
    } else {
      localStorage.removeItem(key)
      setShowLowBalance(false)
    }
  }, [usdcBalance, user.user_id])

  // ── Event banner — edit text/emphasis here to change ────────────────────
  const EVENT_BANNER = {
    active: true,
    text: "We'll handle your trading fees for the first 7 days",
    emphasis: "first 7 days",
  }
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#0a0b0d] flex flex-col">
      <style>{`
        @keyframes giftWiggle {
          0%, 100% { transform: rotate(0deg) translateY(0); }
          15% { transform: rotate(-10deg) translateY(-0.5px); }
          30% { transform: rotate(8deg) translateY(0); }
          45% { transform: rotate(-6deg) translateY(-0.5px); }
          60% { transform: rotate(4deg) translateY(0); }
          75% { transform: rotate(-2deg) translateY(0); }
        }
      `}</style>
      {/* Event banner */}
      {EVENT_BANNER.active && (() => {
        const { text, emphasis } = EVENT_BANNER
        const idx = text.indexOf(emphasis)
        const before = text.slice(0, idx)
        const after  = text.slice(idx + emphasis.length)
        return (
          <div className="flex-shrink-0 flex items-center justify-center gap-0 px-5 py-2 bg-emerald-950/60 border-b border-emerald-500/10">
            <span className="text-[11px] font-semibold text-green-400 tracking-widest uppercase mr-2">New</span>
            <span className="text-[11px] text-white/30 mr-2">·</span>
            <span className="text-[11px] font-medium text-white/85">
              {before}
              <span className="text-white font-semibold">{emphasis}</span>
              {after}
            </span>
          </div>
        )
      })()}
      {/* Top bar */}
      <div className="flex-shrink-0 flex items-center justify-between px-5 h-12 border-b border-white/[0.06]">
        <div className="flex items-center gap-3">
          <img src={tulipLogo} alt="Tulip" className="w-6 h-6 object-contain" />
          <span style={{ fontFamily: 'Fredoka, sans-serif', fontWeight: 700, fontSize: 18 }} className="text-white">tulip</span>
          {strategy && (
            <span className={`text-[10px] px-2 py-0.5 rounded font-mono font-semibold ${
              strategy.available
                ? 'bg-emerald-500/10 text-emerald-400'
                : 'bg-white/5 text-white/25'
            }`}>
              {strategy.available ? 'LIVE' : strategy.global_pause ? 'PAUSED' : 'UNAVAILABLE'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {user.is_admin && (
            <Link to="/admin" className="text-[11px] text-white/25 hover:text-brand transition-colors">
              admin {'->'}
            </Link>
          )}
          <button
            onClick={() => setTab('profile')}
            className="group flex items-center gap-1.5 rounded-full border border-emerald-400/20 bg-emerald-500/[0.12] px-2.5 py-1 text-emerald-300/90 hover:bg-emerald-500/[0.16] hover:border-emerald-300/30 transition-colors"
            title="Open rewards"
          >
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-400/12 text-emerald-300">
              <Sparkles
                size={12}
                strokeWidth={2}
                className="animate-[giftWiggle_2.2s_ease-in-out_infinite] group-hover:animate-[giftWiggle_1s_ease-in-out_infinite]"
              />
            </span>
            <span className="hidden sm:inline text-[11px] font-semibold tracking-wide text-emerald-200/95">Referral campaign</span>
          </button>
          <a
            href="https://discord.gg/y2B8kmAA3n"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center w-7 h-7 rounded-lg text-[#c4254a]/50 hover:text-[#c4254a] hover:bg-white/[0.05] transition-colors"
            title="Discord"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" className="text-[#c4254a]">
              <path d="M20.317 4.492c-1.53-.69-3.17-1.2-4.885-1.49a.075.075 0 0 0-.079.036c-.21.369-.444.85-.608 1.23a18.566 18.566 0 0 0-5.487 0 12.36 12.36 0 0 0-.617-1.23A.077.077 0 0 0 8.562 3c-1.714.29-3.354.8-4.885 1.491a.07.07 0 0 0-.032.027C.533 9.093-.32 13.555.099 17.961a.08.08 0 0 0 .031.055 20.03 20.03 0 0 0 5.993 2.98.078.078 0 0 0 .084-.026c.462-.62.874-1.275 1.226-1.963a.075.075 0 0 0-.041-.104 13.201 13.201 0 0 1-1.872-.878.075.075 0 0 1-.008-.125c.126-.093.252-.19.372-.287a.075.075 0 0 1 .078-.01c3.927 1.764 8.18 1.764 12.061 0a.075.075 0 0 1 .079.009c.12.098.245.195.372.288a.075.075 0 0 1-.006.125c-.598.344-1.22.635-1.873.877a.075.075 0 0 0-.041.105c.36.687.772 1.341 1.225 1.962a.077.077 0 0 0 .084.028 19.963 19.963 0 0 0 6.002-2.981.076.076 0 0 0 .032-.054c.5-5.094-.838-9.52-3.549-13.442a.06.06 0 0 0-.031-.028zM8.02 15.278c-1.182 0-2.157-1.069-2.157-2.38 0-1.312.956-2.38 2.157-2.38 1.21 0 2.176 1.077 2.157 2.38 0 1.312-.956 2.38-2.157 2.38zm7.975 0c-1.183 0-2.157-1.069-2.157-2.38 0-1.312.955-2.38 2.157-2.38 1.21 0 2.176 1.077 2.157 2.38 0 1.312-.946 2.38-2.157 2.38z"/>
            </svg>
          </a>
          <UserMenu
            user={user}
            usdcBalance={usdcBalance}
            feeUsdc={feeUsdc}
            feeEth={feeEth}
            onSignOut={onSignOut}
          />
        </div>
      </div>

      {(needsUsdc || needsEth || ((warnUsdc || warnEth) && !warnDismissed)) && (
        <div className="px-5">
          <div className="mt-3 mb-2 border border-amber-400/20 bg-amber-500/5 rounded-xl px-4 py-3 flex items-center justify-between gap-4 animate-[fadeIn_200ms_ease-out]">
            <div className="text-[11px] text-amber-200/90">
              {fundingMessage || 'Please fund your wallet to continue.'}{' '}
              <button
                type="button"
                onClick={() => setTab('profile')}
                className="underline underline-offset-2 text-amber-200/90 hover:text-amber-100"
              >
                Profile
              </button>
            </div>
            {!needsUsdc && !needsEth && (
              <button
                type="button"
                onClick={() => setWarnDismissed(true)}
                className="flex-shrink-0 text-amber-200/40 hover:text-amber-200/80 transition-colors"
                aria-label="Dismiss"
              >
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M1 1l10 10M11 1L1 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      {showLowBalance && !needsUsdc && !needsEth && !warnUsdc && !warnEth && (
        <div className="px-5">
          <div className="mt-3 mb-2 border border-white/[0.08] bg-[#15161a] rounded-xl px-4 py-3 flex items-center justify-between gap-4 animate-[fadeIn_200ms_ease-out]">
            <div className="text-[11px] text-white/70">
              Your balance is low. Please deposit in Profile to start using our algorithm.
            </div>
            <button
              onClick={() => {
                const key = `low_balance_banner_dismissed_${user.user_id}`
                localStorage.setItem(key, '1')
                setShowLowBalance(false)
              }}
              className="text-[12px] text-white/35 hover:text-white/70 transition-colors"
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        </div>
      )}

      {/* Tab bar */}
      <div className="flex-shrink-0 border-b border-white/[0.06]">
        <div className="w-[80%] mx-auto flex">
          {(['run', 'history', 'performance', 'profile', 'settings'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2.5 text-[11px] font-semibold uppercase tracking-widest transition-colors relative ${
                tab === t ? 'text-white' : 'text-white/25 hover:text-white/50'
              }`}
            >
              {t}
              {tab === t && (
                <span className="absolute bottom-0 left-0 right-0 h-px bg-brand" />
              )}
            </button>
          ))}
          <div className="flex-1" />
          <button
            onClick={() => setTab('faq')}
            className={`px-4 py-2.5 text-[11px] font-semibold uppercase tracking-widest transition-colors relative ${
              tab === 'faq' ? 'text-white' : 'text-white/25 hover:text-white/50'
            }`}
          >
            FAQ
            {tab === 'faq' && (
              <span className="absolute bottom-0 left-0 right-0 h-px bg-brand" />
            )}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto py-5 px-5">
        <div className="w-[80%] mx-auto rounded-xl border border-white/[0.06] bg-[#0d0e10] min-h-[400px]">
          {tab === 'run' && (
            <RunTab
              user={user}
              strategy={strategy}
              activeSession={activeSession}
              loading={loading}
              usdcBalance={usdcBalance}
              onSessionChange={(s) => setActiveSession(s)}
            />
          )}
          {tab === 'history' && <HistoryTab />}
          {tab === 'performance' && <PerformanceTab />}
          {tab === 'profile' && <ProfileTab user={user} />}
          {tab === 'settings' && <SettingsTab user={user} onSignOut={onSignOut} />}
          {tab === 'faq' && <FaqTab />}
        </div>
      </div>
    </div>
  )
}
