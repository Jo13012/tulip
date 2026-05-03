import { useEffect, useState, useRef } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { usePrivy, useLoginWithOAuth } from '@privy-io/react-auth'

import { useAppStore } from './store'
import type { AppUser } from './types'
import { api, initApiAuth } from './utils/api'
import { ToastContainer } from './components/ui/Toast'
import { AdminSidebar } from './components/layout/AdminSidebar'
import { OnboardingModal, getFundingSkipKey } from './components/user/OnboardingModal'
import tulipLogo from './assets/tulip.png'

import UserDashboard from './pages/UserDashboard'
import AdminDashboard  from './pages/admin/Dashboard'
import AdminStrategies from './pages/admin/Strategies'
import AdminSessions   from './pages/admin/Sessions'
import AdminAuditLog   from './pages/admin/AuditLog'
import AdminUsers      from './pages/admin/Users'

// ── Login right-panel slides ──────────────────────────────────────────────────

function SlideStats() {
  return (
    <div className="flex flex-col justify-center h-full px-12 py-10 select-none">
      <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-brand/70 mb-4">Proven performance</div>
      <div className="text-[42px] font-extrabold leading-[1.05] text-white mb-5">
        $500 cost<br />
        <span className="text-white/40">per million.</span>
      </div>
      <p className="text-[14px] text-white/40 leading-relaxed mb-8">
        Only <span className="text-white/70 font-semibold">5 bps</span> on volume — the lowest fees on Polymarket. Tested across 500k+ volume and hundreds of sessions. ~<span className="text-white/70 font-semibold">+0.5% PnL daily</span>.
      </p>
      <div className="grid grid-cols-3 gap-4">
        {[
          { v: '5 bps', l: 'Fee per trade' },
          { v: '+0.5%', l: 'Avg daily PnL' },
          { v: 'x20', l: 'Volume daily based on your size' },
        ].map(({ v, l }) => (
          <div key={l} className="bg-white/[0.04] rounded-xl px-3 py-2.5">
            <div className="text-[18px] font-bold font-mono text-brand">{v}</div>
            <div className="text-[9px] text-white/30 uppercase tracking-widest mt-0.5">{l}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function SlideSimple() {
  return (
    <div className="flex flex-col justify-center h-full px-12 py-10 select-none">
      <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-emerald-400/60 mb-4">Deposit & forget</div>
      <div className="text-[42px] font-extrabold leading-[1.05] text-white mb-5">
        Zero effort.<br />
        <span className="text-white/40">Full automation.</span>
      </div>
      <p className="text-[14px] text-white/40 leading-relaxed mb-8">
        Deposit once. The algorithm handles everything else. 24/7 on Polymarket. <span className="text-white/70 font-semibold">Nothing to manage.</span>
      </p>
      <div className="grid grid-cols-3 gap-4">
        {[
          { v: '24/7', l: 'Runs non-stop' },
          { v: '100%', l: 'Automated' },
          { v: '0', l: 'Actions needed' },
        ].map(({ v, l }) => (
          <div key={l} className="bg-white/[0.04] rounded-xl px-3 py-2.5">
            <div className="text-[18px] font-bold font-mono text-emerald-400">{v}</div>
            <div className="text-[9px] text-white/30 uppercase tracking-widest mt-0.5">{l}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function SlideSecurity() {
  return (
    <div className="flex flex-col justify-center h-full px-12 py-10 select-none">
      <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-amber-400/60 mb-4">Non-custodial</div>
      <div className="text-[42px] font-extrabold leading-[1.05] text-white mb-5">
        Your keys.<br />
        <span className="text-white/40">Always.</span>
      </div>
      <p className="text-[14px] text-white/40 leading-relaxed mb-8">
        We <span className="text-white/70 font-semibold">never ask for, store, or touch</span> your private keys. Everything runs directly on Polymarket through your own wallet. You stay in control at all times.
      </p>
      <div className="grid grid-cols-3 gap-4">
        {[
          { v: '0', l: 'Keys stored' },
          { v: 'Direct', l: 'On Polymarket' },
          { v: '100%', l: 'Non-custodial' },
        ].map(({ v, l }) => (
          <div key={l} className="bg-white/[0.04] rounded-xl px-3 py-2.5">
            <div className="text-[18px] font-bold font-mono text-amber-400">{v}</div>
            <div className="text-[9px] text-white/30 uppercase tracking-widest mt-0.5">{l}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

const SLIDES = [SlideStats, SlideSimple, SlideSecurity]
const SLIDE_DURATION = 5000

function RightPanel() {
  const [idx, setIdx]       = useState(0)
  const [fade, setFade]     = useState(true)
  const timerRef            = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    timerRef.current = setTimeout(() => {
      setFade(false)
      setTimeout(() => {
        setIdx(i => (i + 1) % SLIDES.length)
        setFade(true)
      }, 300)
    }, SLIDE_DURATION)
    return () => clearTimeout(timerRef.current)
  }, [idx])

  const Slide = SLIDES[idx]

  return (
    <div className="relative h-full flex flex-col overflow-hidden">
      {/* Subtle gradient bg that shifts per slide */}
      <div
        className="absolute inset-0 transition-opacity duration-500"
        style={{
          background: idx === 0
            ? 'radial-gradient(ellipse at 70% 40%, rgba(232, 49, 90,0.12) 0%, transparent 65%)'
            : idx === 1
            ? 'radial-gradient(ellipse at 70% 40%, rgba(16,185,129,0.10) 0%, transparent 65%)'
            : 'radial-gradient(ellipse at 70% 40%, rgba(251,191,36,0.08) 0%, transparent 65%)',
        }}
      />

      {/* Content */}
      <div
        className="flex-1 flex flex-col transition-opacity duration-300"
        style={{ opacity: fade ? 1 : 0 }}
      >
        <Slide />
      </div>

      {/* Dots */}
      <div className="flex items-center gap-2 px-12 pb-10">
        {SLIDES.map((_, i) => (
          <button
            key={i}
            onClick={() => { setFade(false); setTimeout(() => { setIdx(i); setFade(true) }, 200) }}
            className={`rounded-full transition-all duration-300 ${
              i === idx ? 'w-4 h-1.5 bg-white/50' : 'w-1.5 h-1.5 bg-white/15 hover:bg-white/30'
            }`}
          />
        ))}
      </div>
    </div>
  )
}

// ── LoginScreen ───────────────────────────────────────────────────────────────

function LoginScreen({
  authenticated,
  onRetry,
  errorMessage = '',
}: {
  authenticated: boolean
  onRetry?: () => void
  errorMessage?: string
}) {
  const { login } = usePrivy()
  const { initOAuth } = useLoginWithOAuth()

  return (
    <div className="min-h-screen flex bg-[#0b0c0f]">
      {/* ── Left: login form ─────────────────────────────────────────────── */}
      <div className="w-full md:w-1/2 md:shrink-0 flex flex-col relative">
        {/* Mobile: subtle top gradient for brand feel */}
        <div className="md:hidden absolute inset-0 pointer-events-none" style={{background:'radial-gradient(ellipse at 50% 0%, rgba(232,49,90,0.10) 0%, transparent 60%)'}} />

        {/* Top bar */}
        <div className="flex items-center gap-2.5 px-8 h-16 shrink-0 relative z-10">
          <img src={tulipLogo} alt="Tulip" className="w-8 h-8 object-contain" />
          <span style={{ fontFamily: 'Fredoka, sans-serif', fontWeight: 700, fontSize: 20, letterSpacing: 0 }} className="text-white">tulip</span>
        </div>

        {/* Form area */}
        <div className="flex-1 flex flex-col justify-center px-8 pb-10 relative z-10">
          <div className="max-w-[340px] w-full mx-auto">
            <h1 className="text-[26px] font-bold text-white mb-1.5">Welcome!</h1>
            <p className="text-[13px] text-white/35 mb-8">
              Log in to Tulip to continue.
            </p>

            {errorMessage && (
              <div className="mb-5 text-[11px] text-red-300/85 bg-red-500/5 border border-red-500/10 rounded-xl px-3.5 py-2.5 break-words">
                {errorMessage}
              </div>
            )}

            {authenticated ? (
              <button
                onClick={onRetry}
                className="w-full h-11 rounded-xl bg-white/[0.07] border border-white/[0.10] text-[13px] font-semibold text-white/80 hover:bg-white/[0.10] hover:border-white/20 transition-all"
              >
                Retry connection
              </button>
            ) : (
              <div className="flex flex-col gap-3">
                {/* Google */}
                <button
                  onClick={() => initOAuth({ provider: 'google' })}
                  className="w-full h-11 rounded-xl bg-[#1a1b1f] border border-white/[0.09] flex items-center justify-center gap-3 text-[13px] font-semibold text-white/80 hover:bg-[#22232a] hover:border-white/[0.15] transition-all"
                >
                  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                    <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z" fill="#4285F4"/>
                    <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" fill="#34A853"/>
                    <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z" fill="#FBBC05"/>
                    <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58Z" fill="#EA4335"/>
                  </svg>
                  Log in with Google
                </button>

                {/* X / Twitter */}
                <button
                  onClick={() => initOAuth({ provider: 'twitter' })}
                  className="w-full h-11 rounded-xl bg-[#1a1b1f] border border-white/[0.09] flex items-center justify-center gap-3 text-[13px] font-semibold text-white/80 hover:bg-[#22232a] hover:border-white/[0.15] transition-all"
                >
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M12.6 1h2.454l-5.36 6.129L16 15h-4.937l-3.864-5.056L2.695 15H.24l5.733-6.553L0 1h5.063l3.495 4.623L12.6 1Zm-.86 12.6h1.36L4.28 2.394H2.818L11.74 13.6Z" fill="white"/>
                  </svg>
                  Log in with X
                </button>

                {/* Email / all options */}
                <button
                  onClick={login}
                  className="w-full h-11 rounded-xl bg-[#1a1b1f] border border-white/[0.09] flex items-center justify-center gap-3 text-[13px] font-semibold text-white/80 hover:bg-[#22232a] hover:border-white/[0.15] transition-all"
                >
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <rect x="1" y="3" width="14" height="10" rx="2" stroke="rgba(255,255,255,0.5)" strokeWidth="1.25"/>
                    <path d="M1 5.5l7 4.5 7-4.5" stroke="rgba(255,255,255,0.5)" strokeWidth="1.25" strokeLinecap="round"/>
                  </svg>
                  Log in with Email
                </button>

                <div className="mt-3 text-center text-[12px] text-white/25">
                  Don't have an account?{' '}
                  <button
                    onClick={login}
                    className="text-white/55 hover:text-white/80 underline underline-offset-2 transition-colors"
                  >
                    Sign up
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Right: slides (desktop only) ─────────────────────────────────── */}
      <div className="hidden md:flex flex-1 sticky top-0 border-l border-white/[0.05] overflow-hidden" style={{height:'100vh'}}>
        <RightPanel />
      </div>
    </div>
  )
}

function LoadingShell() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0f1012]">
      <div className="text-white/20 text-sm">Loading…</div>
    </div>
  )
}

function AdminLayout({ children, onSignOut }: { children: React.ReactNode; onSignOut: () => void }) {
  return (
    <div className="flex min-h-screen">
      <AdminSidebar onSignOut={onSignOut} />
      <main className="flex-1 overflow-y-auto min-h-screen pt-11 md:pt-0">{children}</main>
    </div>
  )
}

function RequireAdmin({ children, isAdmin }: { children: React.ReactNode; isAdmin: boolean }) {
  if (!isAdmin) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  const { ready, authenticated, getAccessToken, logout } = usePrivy()
  const { user, setUser, setStrategy, setActiveSession, clearLogs, clearToasts } = useAppStore()
  const addToast = useAppStore((s) => s.addToast)
  const [bootstrapping, setBootstrapping] = useState(false)
  const [onboardingDone, setOnboardingDone] = useState(false)
  const [bootstrapError, setBootstrapError] = useState('')

  useEffect(() => {
    initApiAuth(() => getAccessToken())
  }, [getAccessToken])

  useEffect(() => {
    try {
      const ref = new URLSearchParams(window.location.search).get('r')?.trim()
      if (ref) {
        sessionStorage.setItem('referral_prefill', ref)
      }
    } catch {}
  }, [])

  async function bootstrap() {
    setBootstrapping(true)
    setBootstrapError('')
    try {
      const profile: any = await api.syncUser()
      setUser(profile)
    } catch (e: any) {
      const message = e?.message || 'Failed to sync account'
      setBootstrapError(message)
      addToast({ type: 'error', message: 'Failed to sync account - ' + message })
    } finally {
      setBootstrapping(false)
    }
  }

  useEffect(() => {
    if (!ready || !authenticated) return
    if (user) return
    bootstrap()
  }, [ready, authenticated, user])

  function clearUserLocalState(_userId: string) {
    try {
      localStorage.clear()
      sessionStorage.clear()
    } catch {}
  }

  function handleSignOut() {
    if (user?.user_id) {
      clearUserLocalState(user.user_id)
    }
    setBootstrapError('')
    setOnboardingDone(false)
    setStrategy(null)
    setActiveSession(null)
    clearLogs()
    clearToasts()
    setUser(null)
    logout()
  }

if (!ready || bootstrapping) return <LoadingShell />
  if (!authenticated) return <LoginScreen authenticated={authenticated} onRetry={bootstrap} />
  if (!user) {
    return <LoginScreen authenticated onRetry={bootstrap} errorMessage={bootstrapError} />
  }

  const displayNameNeedsSetup = !user.display_name ||
    /^0x[0-9a-fA-F]{4}…[0-9a-fA-F]{4}$/.test(user.display_name)
  const fundingSkipped = (() => { try { return !!localStorage.getItem(getFundingSkipKey(user.user_id)) } catch { return false } })()
  // Show onboarding if: username not set (step 1+2 needed) OR funding not done and not skipped (step 3)
  const needsOnboarding = !onboardingDone && (displayNameNeedsSetup || (!user.onboarding_complete && !fundingSkipped))

  const isAdmin = user.is_admin

  return (
    <BrowserRouter>
      {needsOnboarding && (
        <OnboardingModal
          user={user}
          onDone={() => setOnboardingDone(true)}
          onUpdateUser={(partial) => setUser({ ...user, ...partial })}
        />
      )}
      <Routes>
        <Route path="/" element={<UserDashboard user={user} onSignOut={handleSignOut} />} />
        <Route path="/history" element={<Navigate to="/" replace />} />

        <Route path="/admin" element={
          <RequireAdmin isAdmin={isAdmin}>
            <AdminLayout onSignOut={handleSignOut}><AdminDashboard /></AdminLayout>
          </RequireAdmin>
        } />
        <Route path="/admin/strategies" element={
          <RequireAdmin isAdmin={isAdmin}>
            <AdminLayout onSignOut={handleSignOut}><AdminStrategies /></AdminLayout>
          </RequireAdmin>
        } />
        <Route path="/admin/sessions" element={
          <RequireAdmin isAdmin={isAdmin}>
            <AdminLayout onSignOut={handleSignOut}><AdminSessions /></AdminLayout>
          </RequireAdmin>
        } />
        <Route path="/admin/audit" element={
          <RequireAdmin isAdmin={isAdmin}>
            <AdminLayout onSignOut={handleSignOut}><AdminAuditLog /></AdminLayout>
          </RequireAdmin>
        } />
        <Route path="/admin/users" element={
          <RequireAdmin isAdmin={isAdmin}>
            <AdminLayout onSignOut={handleSignOut}><AdminUsers /></AdminLayout>
          </RequireAdmin>
        } />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ToastContainer />
    </BrowserRouter>
  )
}
