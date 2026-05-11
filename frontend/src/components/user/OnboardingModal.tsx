/**
 * OnboardingModal - 3-step onboarding flow.
 *
 * Step 1: Wallet init - polls /onboarding-status with real backend stages
 * Step 2: Username - stores a local draft only
 * Step 3: Funding - instructions only
 *
 * Progress is persisted in localStorage so refresh resumes at the right step.
 */
import { useEffect, useRef, useState } from 'react'
import { usePrivy } from '@privy-io/react-auth'
import { Copy, Check } from 'lucide-react'

import type { AppUser } from '../../types'
import { api } from '../../utils/api'
import { AddressField, GhostButton } from '../ui/Field'

const TRUNCATE_LEN = 120

function ErrorBox({ message }: { message: string }) {
  const [copied, setCopied] = useState(false)
  const truncated = message.length > TRUNCATE_LEN ? message.slice(0, TRUNCATE_LEN) + '…' : message
  function copy() {
    navigator.clipboard.writeText(message).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <div className="mt-1 flex items-start gap-2 text-[10px] text-red-300/80 bg-red-500/5 border border-red-500/10 rounded-lg px-3 py-2 leading-relaxed">
      <span className="flex-1 break-all">{truncated}</span>
      <button onClick={copy} className="flex-shrink-0 text-red-300/40 hover:text-red-300/80 transition-colors mt-0.5">
        {copied ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} />}
      </button>
    </div>
  )
}

const USERNAME_REGEX = /^[A-Za-z0-9]+$/

type OnboardingState = 'pending' | 'deploying' | 'approving' | 'finalizing' | 'ready' | 'failed' | 'no_wallet'
type CheckStatus = 'idle' | 'pending' | 'active' | 'ok' | 'failed'
type Step = 1 | 2 | 3 | 4

const CHECKS: { id: string; labelDone: string; labelActive: string; labelPending: string }[] = [
  { id: 'connect',  labelDone: 'Network connected',      labelActive: 'Connecting…',        labelPending: 'Network'        },
  { id: 'verify',   labelDone: 'Credentials verified',   labelActive: 'Verifying keys…',    labelPending: 'Credentials'    },
  { id: 'wallet',   labelDone: 'Wallet ready',           labelActive: 'Deploying wallet…',  labelPending: 'Wallet'         },
  { id: 'prepare',  labelDone: 'Account configured',     labelActive: 'Setting approvals…', labelPending: 'Account'        },
  { id: 'finalize', labelDone: 'Setup complete',         labelActive: 'Wrapping up…',       labelPending: 'Finalize'       },
]

function looksLikeAutoDisplayName(displayName: string) {
  const trimmed = displayName.trim()
  if (!trimmed) return false
  return /^0x[0-9a-fA-F]{4}.*[0-9a-fA-F]{4}$/.test(trimmed)
}

function loadDraftDisplayName(user: AppUser) {
  const existing = user.display_name?.trim() || ''
  if (existing && !looksLikeAutoDisplayName(existing)) {
    return existing
  }

  return ''
}

function loadDraftReferrer(user: AppUser) {
  const existing = user.referrer_username?.trim()
  if (existing) return existing

  try {
    const fromSession = sessionStorage.getItem('referral_prefill')?.trim() || ''
    if (fromSession) return fromSession.replace(/[^A-Za-z0-9]/g, '').slice(0, 15)
  } catch {}

  try {
    const fromUrl = new URLSearchParams(window.location.search).get('r')?.trim() || ''
    if (fromUrl) return fromUrl.replace(/[^A-Za-z0-9]/g, '').slice(0, 15)
  } catch {}

  return ''
}

function validateDisplayName(value: string) {
  const trimmed = value.trim()
  if (!trimmed) return 'Username is required'
  if (trimmed.length < 3) return 'At least 3 characters'
  if (trimmed.length > 15) return 'Max 15 characters'
  if (!USERNAME_REGEX.test(trimmed)) return 'Letters and numbers only'
  return ''
}



function StepDots({
  current,
}: {
  current: Step
}) {
  return (
    <div className="flex items-center justify-center gap-2 mt-6">
      {([1, 2, 3, 4] as Step[]).map((step) => {
        const isCurrent = step === current
        return (
          <div
            key={step}
            aria-label={`Step ${step}`}
            className={`rounded-full transition-all duration-300 ${
              isCurrent
                ? 'w-2.5 h-2.5 bg-white'
                : 'w-2 h-2 bg-white/30'
            }`}
          />
        )
      })}
    </div>
  )
}

function Step1Loading({
  onDone,
  disabled,
}: {
  onDone: () => void
  disabled?: boolean
}) {
  const [walletState, setWalletState] = useState<OnboardingState>('pending')
  const [errorDetail, setErrorDetail] = useState('')
  const [checks, setChecks] = useState<Record<string, { status: CheckStatus; reason: string }>>({
    connect: { status: 'pending', reason: '' },
    verify: { status: 'pending', reason: '' },
    wallet: { status: 'pending', reason: '' },
    prepare: { status: 'pending', reason: '' },
    finalize: { status: 'pending', reason: '' },
  })
  const [elapsed, setElapsed] = useState(0)
  const startMs = useRef(Date.now())
  const doneRef = useRef(false)

  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(Math.round((Date.now() - startMs.current) / 1000))
    }, 500)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (disabled) return
    let interval: ReturnType<typeof setInterval>

    const poll = async () => {
      try {
        const res = await api.getOnboardingStatus() as any
        setWalletState(res.state)
        setErrorDetail(res.error_detail || '')
        if (res.checks) {
          setChecks(res.checks)
        }

        if (res.state === 'ready' || res.state === 'failed' || res.state === 'no_wallet') {
          clearInterval(interval)
          maybeAdvance(res.state === 'ready')
        }
      } catch {}
    }

    poll()
    interval = setInterval(poll, 3000)
    return () => clearInterval(interval)
  }, [disabled])

  function maybeAdvance(success: boolean) {
    if (!success) return

    setTimeout(() => {
      if (!doneRef.current) {
        doneRef.current = true
        onDone()
      }
    }, 250)
  }

  const isFailed = walletState === 'failed' || walletState === 'no_wallet'
  const failedCheck = CHECKS.find((check) => checks[check.id]?.status === 'failed')
  const failedReason = failedCheck ? checks[failedCheck.id]?.reason : errorDetail

  return (
    <div className="flex flex-col flex-1">
      <div className="mb-7">
        <div className="text-[10px] text-white/20 uppercase tracking-widest font-semibold mb-1.5">
          Account Setup
        </div>
        <div className="text-[15px] font-semibold text-white leading-snug">
          {isFailed ? 'Setup failed' : 'Setting up your account...'}
        </div>
        {!isFailed && (
          <div className="text-[11px] text-white/25 mt-1 font-mono">{elapsed}s</div>
        )}
      </div>

      <div className="space-y-3 flex-1">
        {CHECKS.map((check, index) => {
          const checkState = checks[check.id]?.status ?? (index === 0 ? 'active' : 'pending')
          const done = checkState === 'ok'
          const active = checkState === 'active'
          const failed = checkState === 'failed'
          const reason = checks[check.id]?.reason || ''
          const isLast = index === CHECKS.length - 1

          return (
            <div key={check.id}>
              <div className="flex items-start gap-3">
                {/* Icon */}
                <div className="w-5 flex-shrink-0 flex flex-col items-center">
                  <div className="w-5 h-5 flex items-center justify-center">
                    {done ? (
                      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-emerald-500/15">
                        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                          <path d="M2 5l2.5 2.5L8 3" stroke="#10b981" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                        </svg>
                      </span>
                    ) : failed ? (
                      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-red-500/15">
                        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                          <path d="M2 2l6 6M8 2L2 8" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round"/>
                        </svg>
                      </span>
                    ) : active ? (
                      <svg className="animate-spin" width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <circle cx="8" cy="8" r="6" stroke="#facc15" strokeWidth="2" strokeOpacity="0.2"/>
                        <path d="M8 2a6 6 0 0 1 6 6" stroke="#facc15" strokeWidth="2" strokeLinecap="round"/>
                      </svg>
                    ) : (
                      <div className="w-1.5 h-1.5 rounded-full bg-white/15" />
                    )}
                  </div>
                </div>
                {/* Text */}
                <div className="flex-1">
                  <span className={`text-[12px] font-medium transition-colors ${
                    done ? 'text-white/50' : failed ? 'text-red-300' : active ? 'text-white' : 'text-white/20'
                  }`}>
                    {done ? check.labelDone : active ? check.labelActive : check.labelPending}
                  </span>
                  {failed && reason && <ErrorBox message={reason} />}
                </div>
              </div>
              {!isLast && (
                <div className="flex gap-3">
                  <div className="w-5 flex-shrink-0 flex justify-center" style={{ marginTop: '2px', marginBottom: '2px' }}>
                    <div className="w-px h-3" style={{
                      backgroundImage: done
                        ? 'repeating-linear-gradient(to bottom, rgba(99,179,237,0.35) 0px, rgba(99,179,237,0.35) 3px, transparent 3px, transparent 6px)'
                        : 'repeating-linear-gradient(to bottom, rgba(255,255,255,0.07) 0px, rgba(255,255,255,0.07) 3px, transparent 3px, transparent 6px)'
                    }} />
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {!isFailed && (
        <div className="mt-5 h-px bg-white/[0.06] rounded overflow-hidden">
          <div
            className="h-full bg-brand/60 transition-all duration-500"
            style={{ width: `${Math.round((CHECKS.filter((check) => checks[check.id]?.status === 'ok').length / CHECKS.length) * 100)}%` }}
          />
        </div>
      )}

      {isFailed && !failedCheck && (
        <ErrorBox message={errorDetail || 'Setup failed. Please sign out and try again.'} />
      )}

      <div className="mt-5 flex justify-end items-center">
        {!isFailed && (
          <div className="text-[10px] text-white/15">~30s on first login</div>
        )}
      </div>
    </div>
  )
}

function Step1Review({
  onContinue,
}: {
  onContinue: () => void
}) {
  return (
    <div className="flex flex-col flex-1">
      <div className="mb-7">
        <div className="text-[10px] text-white/20 uppercase tracking-widest font-semibold mb-1.5">
          Account Setup
        </div>
        <div className="text-[15px] font-semibold text-white leading-snug">
          Account ready
        </div>
        <div className="text-[11px] text-white/35 mt-1">
          Your wallet is set up and ready for the next steps.
        </div>
      </div>

      <div className="space-y-3 flex-1">
        {CHECKS.map((check) => (
          <div key={check.id} className="flex items-center gap-3">
            <div className="w-4 h-4 flex-shrink-0 flex items-center justify-center">
              <span className="text-emerald-400 text-[11px]">OK</span>
            </div>
            <span className="text-[12px] text-white">{check.labelDone}</span>
          </div>
        ))}
      </div>

      <div className="mt-5 flex justify-end items-center">
        <button
          type="button"
          onClick={onContinue}
          className="text-[10px] text-white/25 hover:text-white/50 transition-colors"
        >
          Continue
        </button>
      </div>
    </div>
  )
}

function Step2Referral({
  value,
  onChange,
  onContinue,
  onSkip,
  lockedUsername,
  serverError,
}: {
  value: string
  onChange: (value: string) => void
  onContinue: (value: string) => void
  onSkip: () => void
  lockedUsername?: string | null
  serverError: string
}) {
  const isLocked = Boolean(lockedUsername)

  return (
    <div className="flex flex-col flex-1">
      <div className="mb-7">
        <div className="text-[10px] text-white/20 uppercase tracking-widest font-semibold mb-1.5">
          Referral
        </div>
        <div className="text-[15px] font-semibold text-white">Add a referrer</div>
        <div className="text-[11px] text-white/35 mt-1">
          Optional. You can skip this step for now.
        </div>
      </div>

      <div className="flex-1 flex flex-col justify-center gap-2">
        <input
          type="text"
          value={isLocked ? (lockedUsername || '') : value}
          onChange={(e) => onChange(e.target.value.replace(/[^A-Za-z0-9]/g, ''))}
          onKeyDown={(e) => e.key === 'Enter' && !isLocked && onContinue(value)}
          placeholder="Referrer username"
          maxLength={15}
          disabled={isLocked}
          className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2.5 text-[13px] text-white placeholder-white/20 outline-none transition-colors disabled:opacity-70 disabled:cursor-not-allowed"
        />
        <div className="text-[10px] text-white/20">
          The referrer automatically receives 10% of paid fees in USDC. Not required.
        </div>
        {serverError && (
          <div className="text-[11px] text-red-400">{serverError}</div>
        )}
      </div>

      <div className="mt-5 flex justify-between items-center gap-3">
        {!isLocked ? (
          <button
            type="button"
            onClick={onSkip}
            className="text-[10px] text-white/20 hover:text-white/45 transition-colors"
          >
            Skip for now
          </button>
        ) : <span />}
        <button
          type="button"
          onClick={() => onContinue(isLocked ? (lockedUsername || '') : value)}
          className="text-[10px] text-white/25 hover:text-white/50 transition-colors"
        >
          Continue
        </button>
      </div>
    </div>
  )
}

function Step2Username({
  value,
  onChange,
  onDone,
  serverError,
}: {
  value: string
  onChange: (value: string) => void
  onDone: (value: string) => void
  serverError: string
}) {
  const [error, setError] = useState('')

  useEffect(() => {
    setError('')
  }, [value])

  function handleSubmit() {
    const trimmed = value.trim()
    const validationError = validateDisplayName(trimmed)
    if (validationError) {
      setError(validationError)
      return
    }

    onDone(trimmed)
  }

  return (
    <div className="flex flex-col flex-1">
      <div className="mb-7">
        <div className="text-[10px] text-white/20 uppercase tracking-widest font-semibold mb-1.5">
          Welcome
        </div>
        <div className="text-[15px] font-semibold text-white">Choose your username</div>
        <div className="text-[11px] text-white/35 mt-1">
          This is how you&apos;ll appear on the platform
        </div>
      </div>

      <div className="flex-1 flex flex-col justify-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/[^A-Za-z0-9]/g, ''))}
          onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
          placeholder="Username"
          maxLength={15}
          autoFocus
          className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2.5 text-[13px] text-white placeholder-white/20 outline-none transition-colors ${
            error ? 'border-red-500/50 focus:border-red-500/70' : 'border-white/[0.08] focus:border-white/20'
          }`}
        />
        {(error || serverError) && (
          <div className="text-[11px] text-red-400">{error || serverError}</div>
        )}
        <div className="text-[10px] text-white/20">
          3–15 letters or numbers, no spaces
        </div>
      </div>

      <div className="mt-5 flex justify-end items-center">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!value.trim()}
          className="text-[10px] text-white/25 hover:text-white/50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Continue
        </button>
      </div>
    </div>
  )
}

const CHAIN_LABELS: Record<string, string> = {
  '42161': 'Arbitrum',
  '8453':  'Base',
  'solana': 'Solana',
}

export function Step3Deposit({
  depositConfig,
  isFunded,
  onFinish,
  onSkip,
}: {
  depositConfig: import('../../types').DepositConfigOut | null
  isFunded: boolean
  onFinish: () => void
  onSkip?: () => void
}) {
  const [selectedChainId, setSelectedChainId] = useState<string>('42161')
  const [copiedAddr, setCopiedAddr] = useState(false)

  const chain = depositConfig?.chains.find(c => c.chain_id === selectedChainId)
    ?? depositConfig?.chains[0]
  const depositAddr = chain?.address_type === 'svm'
    ? (depositConfig?.deposit_addresses.svm ?? '')
    : (depositConfig?.deposit_addresses.evm ?? '')

  function copyAddr() {
    if (!depositAddr) return
    navigator.clipboard.writeText(depositAddr).then(() => {
      setCopiedAddr(true)
      setTimeout(() => setCopiedAddr(false), 2000)
    })
  }

  const notReady = !depositConfig?.account_ready

  return (
    <div className="flex flex-col flex-1">
      <div className="mb-6">
        <div className="text-[10px] text-white/20 uppercase tracking-widest font-semibold mb-1.5">
          Fund account
        </div>
        <div className="text-[15px] font-semibold text-white">
          {isFunded ? 'Account funded' : 'Deposit to Polymarket'}
        </div>
        <div className="text-[11px] text-white/35 mt-1">
          {isFunded
            ? 'Your Polymarket balance is ready.'
            : 'Send USDC directly to your Polymarket deposit address.'}
        </div>
      </div>

      <div className="space-y-4 flex-1">
        {notReady ? (
          <div className="text-[11px] text-white/35 py-4 text-center">
            Complete account setup to get your deposit address.
          </div>
        ) : !depositConfig ? (
          <div className="text-[11px] text-amber-400/60 py-4 text-center">
            Deposit temporarily unavailable. Try again later.
          </div>
        ) : (
          <>
            {/* Chain selector */}
            <div className="flex gap-2">
              {depositConfig.chains.map(c => (
                <button
                  key={c.chain_id}
                  type="button"
                  onClick={() => setSelectedChainId(c.chain_id)}
                  className={`flex-1 text-[10px] py-1.5 rounded-lg border transition-all ${
                    selectedChainId === c.chain_id
                      ? 'border-white/30 bg-white/10 text-white'
                      : 'border-white/[0.07] bg-transparent text-white/30 hover:text-white/60'
                  }`}
                >
                  {CHAIN_LABELS[c.chain_id] ?? c.chain_name}
                </button>
              ))}
            </div>

            {/* Deposit address */}
            <div className="rounded-xl border border-white/[0.07] bg-[#0d0e10] overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3">
                <span className="text-[11px] text-white/40 flex-shrink-0">Deposit to</span>
                <div className="flex-1 min-w-0">
                  {depositAddr
                    ? <AddressField value={depositAddr} />
                    : <span className="text-[11px] text-white/20 font-mono">—</span>
                  }
                </div>
                {depositAddr && (
                  <button
                    type="button"
                    onClick={copyAddr}
                    className="flex-shrink-0 text-white/25 hover:text-white/60 transition-colors ml-1"
                  >
                    {copiedAddr ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
                  </button>
                )}
              </div>
              {chain && (
                <div className="border-t border-white/[0.05] px-4 py-2.5">
                  <div className="flex flex-wrap gap-x-3 gap-y-1">
                    {chain.tokens.map(tok => (
                      <span key={tok.symbol} className="text-[10px] text-white/30">
                        {tok.symbol}
                        {tok.min_checkout_usd != null && (
                          <span className="text-white/20"> (min ${tok.min_checkout_usd})</span>
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Balance */}
            {depositConfig.trading_balance != null && (
              <div className="flex items-center justify-between px-1">
                <span className="text-[10px] text-white/30">Polymarket balance</span>
                <span className={`text-[11px] font-mono ${isFunded ? 'text-emerald-400' : 'text-white/40'}`}>
                  ${(depositConfig.trading_balance + depositConfig.positions_value).toFixed(2)}
                </span>
              </div>
            )}

            <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-white/[0.07] bg-white/[0.02]">
              <p className="text-[10px] text-white/35 leading-relaxed">
                Deposits are credited directly on Polymarket. No bridge required. Funds appear within a few minutes.
              </p>
            </div>
          </>
        )}
      </div>

      <div className="mt-5 flex justify-between items-center">
        {!isFunded && onSkip ? (
          <button
            type="button"
            onClick={onSkip}
            className="text-[10px] text-white/20 hover:text-white/45 transition-colors"
          >
            Skip for now
          </button>
        ) : <span />}
        {isFunded && (
          <GhostButton onClick={onFinish}>Continue</GhostButton>
        )}
      </div>

      <div className="mt-4 flex items-center gap-2">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" className="text-[#c4254a]/60 flex-shrink-0">
          <path d="M20.317 4.492c-1.53-.69-3.17-1.2-4.885-1.49a.075.075 0 0 0-.079.036c-.21.369-.444.85-.608 1.23a18.566 18.566 0 0 0-5.487 0 12.36 12.36 0 0 0-.617-1.23A.077.077 0 0 0 8.562 3c-1.714.29-3.354.8-4.885 1.491a.07.07 0 0 0-.032.027C.533 9.093-.32 13.555.099 17.961a.08.08 0 0 0 .031.055 20.03 20.03 0 0 0 5.993 2.98.078.078 0 0 0 .084-.026c.462-.62.874-1.275 1.226-1.963a.075.075 0 0 0-.041-.104 13.201 13.201 0 0 1-1.872-.878.075.075 0 0 1-.008-.125c.126-.093.252-.19.372-.287a.075.075 0 0 1 .078-.01c3.927 1.764 8.18 1.764 12.061 0a.075.075 0 0 1 .079.009c.12.098.245.195.372.288a.075.075 0 0 1-.006.125c-.598.344-1.22.635-1.873.877a.075.075 0 0 0-.041.105c.36.687.772 1.341 1.225 1.962a.077.077 0 0 0 .084.028 19.963 19.963 0 0 0 6.002-2.981.076.076 0 0 0 .032-.054c.5-5.094-.838-9.52-3.549-13.442a.06.06 0 0 0-.031-.028zM8.02 15.278c-1.182 0-2.157-1.069-2.157-2.38 0-1.312.956-2.38 2.157-2.38 1.21 0 2.176 1.077 2.157 2.38 0 1.312-.956 2.38-2.157 2.38zm7.975 0c-1.183 0-2.157-1.069-2.157-2.38 0-1.312.955-2.38 2.157-2.38 1.21 0 2.176 1.077 2.157 2.38 0 1.312-.946 2.38-2.157 2.38z"/>
        </svg>
        <span className="text-[10px] text-white/25">Need support?</span>
        <a
          href="https://discord.gg/y2B8kmAA3n"
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] font-semibold text-[#c4254a]/60 hover:text-[#c4254a]/90 transition-colors"
        >
          Join our Discord →
        </a>
      </div>
    </div>
  )
}

export function FundingModal({
  user,
  onClose,
  onDone,
}: {
  user: AppUser
  onClose: () => void
  onDone?: () => void
}) {
  const [depositConfig, setDepositConfig] = useState<import('../../types').DepositConfigOut | null>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  useEffect(() => {
    let mounted = true
    const load = async () => {
      try {
        const cfg = await api.getDepositConfig()
        if (mounted) setDepositConfig(cfg)
      } catch {}
    }
    load()
    const iv = setInterval(load, 10000)
    return () => { mounted = false; clearInterval(iv) }
  }, [])

  function handleFinish() {
    try { localStorage.removeItem(getFundingSkipKey(user.user_id)) } catch {}
    onDone?.()
    onClose()
  }

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm px-4 py-6"
      onClick={e => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className="w-full max-w-[420px] bg-[#111215] border border-white/[0.08] rounded-2xl p-5 sm:p-6 shadow-2xl flex flex-col max-h-[calc(100vh-3rem)] overflow-hidden">
        <div className="flex items-center justify-between mb-4">
          <span className="text-[9px] font-semibold uppercase tracking-widest text-white/20">Fund Account</span>
          <button className="w-6 h-6 flex items-center justify-center rounded-lg text-white/20 hover:text-white/55 hover:bg-white/[0.06] transition-all" onClick={onClose}>✕</button>
        </div>
        <div className="flex-1 flex flex-col overflow-y-auto min-h-0">
          <Step3Deposit
            depositConfig={depositConfig}
            isFunded={Boolean(depositConfig?.is_funded_now)}
            onFinish={handleFinish}
          />
        </div>
      </div>
    </div>
  )
}

export function getFundingSkipKey(userId: string) {
  return `onboarding_funding_skipped_${userId}`
}

interface Props {
  user: AppUser
  onDone: () => void
  onUpdateUser: (u: Partial<AppUser>) => void
  bootstrapping?: boolean
}

export function OnboardingModal({ user, onDone, onUpdateUser, bootstrapping = false }: Props) {
  usePrivy()
  const initialDraftDisplayName = loadDraftDisplayName(user)
  const initialDraftReferrer = loadDraftReferrer(user)
  const [step, setStep] = useState<Step>(1)
  const [draftDisplayName, setDraftDisplayName] = useState(initialDraftDisplayName)
  const [draftReferrer, setDraftReferrer] = useState(initialDraftReferrer)
  const [visible, setVisible] = useState(true)
  const [serverError, setServerError] = useState('')
  const [depositConfig, setDepositConfig] = useState<import('../../types').DepositConfigOut | null>(null)

  useEffect(() => {
    if (serverError) setServerError('')
  }, [draftDisplayName, draftReferrer])

  useEffect(() => {
    if (bootstrapping || step !== 4) return
    let mounted = true
    const load = async () => {
      try {
        const cfg = await api.getDepositConfig()
        if (mounted) setDepositConfig(cfg)
      } catch {}
    }
    load()
    const iv = setInterval(load, 10000)
    return () => { mounted = false; clearInterval(iv) }
  }, [bootstrapping, step])

  async function handleWalletReady() {
    if (bootstrapping) return
    if (user.referrer_username) {
      setStep(displayNameNeedsSetup ? 3 : 4)
      return
    }
    setStep(2)
  }

  function clearStoredReferralPrefill() {
    try { sessionStorage.removeItem('referral_prefill') } catch {}
  }

  function handleReferralSkip() {
    clearStoredReferralPrefill()
    setStep(displayNameNeedsSetup ? 3 : 4)
  }

  async function handleReferralContinue(nextReferrer: string) {
    setServerError('')
    if (user.referrer_username) {
      setStep(displayNameNeedsSetup ? 3 : 4)
      return
    }

    const trimmed = nextReferrer.trim()
    if (!trimmed) {
      handleReferralSkip()
      return
    }

    try {
      await api.lookupReferrer(trimmed)
      const assigned = await api.assignReferrer(trimmed) as any
      onUpdateUser({ referrer_username: assigned.referrer_username || trimmed })
      setDraftReferrer(assigned.referrer_username || trimmed)
      clearStoredReferralPrefill()
      setStep(displayNameNeedsSetup ? 3 : 4)
    } catch (e: any) {
      setServerError(e?.message || 'Unable to set this referrer.')
    }
  }

  async function handleUsernameContinue(nextDisplayName: string) {
    setServerError('')
    const trimmed = nextDisplayName.trim()
    const validationError = validateDisplayName(trimmed)
    if (validationError) {
      setServerError(validationError)
      return
    }
    try {
      const updated = await api.updateProfile(trimmed) as any
      onUpdateUser({
        display_name: updated.display_name,
        referrer_username: updated.referrer_username,
        referral_reward_usdc: updated.referral_reward_usdc,
      })
      setDraftDisplayName(updated.display_name || trimmed)
      setStep(4)
    } catch (e: any) {
      setServerError(e.message || 'Failed to save username')
    }
  }

  function handleFinishOnboarding() {
    onUpdateUser({ onboarding_complete: true })
    try { localStorage.removeItem(getFundingSkipKey(user.user_id)) } catch {}
    setVisible(false)
    setTimeout(onDone, 0)
  }

  function handleSkipFunding() {
    try { localStorage.setItem(getFundingSkipKey(user.user_id), '1') } catch {}
    setVisible(false)
    setTimeout(onDone, 0)
  }

  const displayNameNeedsSetup = !user.display_name || looksLikeAutoDisplayName(user.display_name)

  if (!visible) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm px-4 py-6">
      <div className="w-full max-w-[420px] bg-[#111215] border border-white/[0.08] rounded-2xl p-5 sm:p-6 shadow-2xl flex flex-col max-h-[calc(100vh-3rem)] overflow-hidden">
        <div className="flex-1 flex flex-col overflow-y-auto min-h-0">
          {step === 1 && (
            <Step1Loading onDone={handleWalletReady} disabled={bootstrapping} />
          )}
          {step === 2 && (
            <Step2Referral
              value={draftReferrer}
              onChange={setDraftReferrer}
              onContinue={handleReferralContinue}
              onSkip={handleReferralSkip}
              lockedUsername={user.referrer_username}
              serverError={serverError}
            />
          )}
          {step === 3 && (
            <Step2Username
              value={draftDisplayName}
              onChange={setDraftDisplayName}
              onDone={handleUsernameContinue}
              serverError={serverError}
            />
          )}
          {step === 4 && (
            <Step3Deposit
              depositConfig={depositConfig}
              isFunded={Boolean(depositConfig?.is_funded_now)}
              onFinish={handleFinishOnboarding}
              onSkip={handleSkipFunding}
            />
          )}
        </div>

        <StepDots current={step} />
      </div>
    </div>
  )
}
