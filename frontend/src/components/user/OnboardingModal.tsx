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
import { useAppStore } from '../../store'
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
type Step = 1 | 2 | 3

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
      {([1, 2, 3] as Step[]).map((step) => {
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

function Step3Funding({
  walletAddress,
  minUsdc,
  minEth,
  walletFunded,
  depositTxHash,
  usdcBalance,
  ethBalance,
  requiredUsdc,
  requiredEth,
  tradingBalance,
  onboardingComplete,
  fundingState,
  canFixFunding,
  onFixFunding,
  bridgeTxHash,
  bridgeStartedAt,
  onFinish,
  feeWalletUsdcMin,
  feeWalletUsdcWarn,
  feeWalletEthMin,
  feeWalletEthWarn,
}: {
  walletAddress: string
  minUsdc: number
  minEth: number
  walletFunded: boolean
  depositTxHash: string
  usdcBalance: number
  ethBalance: number
  requiredUsdc: number
  requiredEth: number
  tradingBalance: number
  onboardingComplete: boolean
  fundingState: string
  canFixFunding: boolean
  onFixFunding: () => void
  bridgeTxHash: string
  bridgeStartedAt: string
  onFinish: () => void
  feeWalletUsdcMin: number
  feeWalletUsdcWarn: number
  feeWalletEthMin: number
  feeWalletEthWarn: number
}) {
  const tradingUpdated = onboardingComplete || tradingBalance > 0
  const bridgeStarted = Boolean(
    bridgeTxHash
    || fundingState === 'bridging'
    || fundingState === 'completed'
    || Boolean(bridgeStartedAt)
    || tradingUpdated
  )
  const bridgeElapsedSec = bridgeStartedAt
    ? Math.max(0, (Date.now() - new Date(bridgeStartedAt).getTime()) / 1000)
    : null
  // Show retry button only if bridge has been running > 2 min or explicitly failed with no active bridge
  const bridgeStuck = fundingState === 'failed' && !tradingUpdated && (
    bridgeElapsedSec === null || bridgeElapsedSec >= 120
  )

  const bridgeLabelDone = tradingUpdated ? 'Sent to Polymarket' : 'Sending to Polymarket…'

  type CheckItem = { id: string; labelDone: string; labelActive: string; labelPending: string; done: boolean; active: boolean }
  const checks: CheckItem[] = [
    { id: 'deposit', labelDone: 'Deposit received',      labelActive: 'Waiting for deposit…',   labelPending: 'Deposit',  done: walletFunded,   active: !walletFunded },
    { id: 'bridge',  labelDone: bridgeLabelDone,         labelActive: 'Bridging to Polymarket…', labelPending: 'Bridge',   done: bridgeStarted,  active: walletFunded && !bridgeStarted },
    { id: 'trading', labelDone: 'Trading balance ready', labelActive: 'Waiting for bridge…',     labelPending: 'Trading',  done: tradingUpdated, active: bridgeStarted && !tradingUpdated },
  ]

  const progressPct = tradingUpdated ? 100 : bridgeStarted ? 66 : walletFunded ? 33 : 0

  return (
    <div className="flex flex-col flex-1">
      <div className="mb-6">
        <div className="text-[10px] text-white/20 uppercase tracking-widest font-semibold mb-1.5">
          Fund account
        </div>
        <div className="text-[15px] font-semibold text-white">Fund your wallet</div>
        <div className="text-[11px] text-white/35 mt-1">
          Send USDC + ETH on Arbitrum. We&apos;ll handle the rest.
        </div>
      </div>

      <div className="space-y-4 flex-1">
        {/* Deposit address */}
        <div className="rounded-xl border border-white/[0.07] bg-[#0d0e10] divide-y divide-white/[0.05] overflow-hidden">
          <div className="flex items-center gap-4 px-4 py-3">
            <span className="text-[11px] text-white/40 flex-shrink-0 w-24">Deposit to</span>
            <div className="flex-1 min-w-0">
              {walletAddress
                ? <AddressField value={walletAddress} />
                : <span className="text-[11px] text-white/20 font-mono">—</span>
              }
            </div>
          </div>
          <div className="flex items-center justify-between gap-4 px-4 py-3">
            <span className="text-[11px] text-white/40 flex-shrink-0 w-24">Balance</span>
            <span className="text-[11px] font-mono text-white/55">${usdcBalance.toFixed(2)} USDC · {ethBalance.toFixed(4)} ETH</span>
          </div>
        </div>

        {/* Warning */}
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-amber-400/20 bg-amber-500/5">
          <span className="text-[10px] text-amber-400/50 mt-0.5 flex-shrink-0">⚠</span>
          <p className="text-[10px] text-amber-300/60 leading-relaxed">
            Minimum deposit: <strong>$20 USDC</strong> + {feeWalletEthWarn.toFixed(4)} ETH for gas.
          </p>
        </div>

        {/* Progress checks */}
        <div>
          {checks.map((check, index) => {
            const isLast = index === checks.length - 1
            return (
              <div key={check.id}>
                <div className="flex items-start gap-3">
                  <div className="w-5 flex-shrink-0 flex flex-col items-center">
                    <div className="w-5 h-5 flex items-center justify-center">
                      {check.done ? (
                        <span className="flex items-center justify-center w-5 h-5 rounded-full bg-emerald-500/15">
                          <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                            <path d="M2 5l2.5 2.5L8 3" stroke="#10b981" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        </span>
                      ) : check.active ? (
                        <svg className="animate-spin" width="16" height="16" viewBox="0 0 16 16" fill="none">
                          <circle cx="8" cy="8" r="6" stroke="#facc15" strokeWidth="2" strokeOpacity="0.2"/>
                          <path d="M8 2a6 6 0 0 1 6 6" stroke="#facc15" strokeWidth="2" strokeLinecap="round"/>
                        </svg>
                      ) : (
                        <div className="w-1.5 h-1.5 rounded-full bg-white/15" />
                      )}
                    </div>
                  </div>
                  <div className="flex-1">
                    <span className={`text-[12px] font-medium transition-colors ${check.done ? 'text-white/50' : check.active ? 'text-white' : 'text-white/20'}`}>
                      {check.done ? check.labelDone : check.active ? check.labelActive : check.labelPending}
                    </span>
                    {check.id === 'bridge' && bridgeTxHash && (
                      <div className="mt-1 flex items-center gap-2">
                        <a
                          href={`https://arbiscan.io/tx/${bridgeTxHash}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[10px] text-white/40 hover:text-white/70 underline underline-offset-2 font-mono"
                        >
                          {bridgeTxHash.slice(0, 8)}…{bridgeTxHash.slice(-6)}
                        </a>
                        {bridgeElapsedSec !== null && (
                          <span className="text-[10px] text-white/30 font-mono">{bridgeElapsedSec.toFixed(0)}s</span>
                        )}
                      </div>
                    )}
                    {check.id === 'bridge' && bridgeStuck && (
                      <div className="mt-1.5">
                        <GhostButton warn disabled={!canFixFunding} onClick={onFixFunding}>
                          Bridge to Polymarket
                        </GhostButton>
                      </div>
                    )}
                  </div>
                </div>
                {!isLast && (
                  <div className="flex gap-3">
                    <div className="w-5 flex-shrink-0 flex justify-center" style={{ marginTop: '2px', marginBottom: '2px' }}>
                      <div className="w-px h-3" style={{
                        backgroundImage: check.done
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

        <div className="h-px bg-white/[0.06] rounded overflow-hidden">
          <div
            className={`h-full transition-all duration-500 ${tradingUpdated ? 'bg-emerald-400/60' : 'bg-brand/60'}`}
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      <div className="mt-5 flex justify-between items-center">
        <div className="text-[10px] text-white/20">
          {tradingUpdated ? 'Ready' : bridgeStarted ? 'Bridging…' : walletFunded ? 'Preparing bridge…' : ''}
        </div>
        {tradingUpdated && (
          <GhostButton onClick={onFinish}>Continue</GhostButton>
        )}
      </div>
    </div>
  )
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
  const [step, setStep] = useState<Step>(1)
  const [draftDisplayName, setDraftDisplayName] = useState(initialDraftDisplayName)
  const [visible, setVisible] = useState(true)
  const [serverError, setServerError] = useState('')
  const [minUsdc, setMinUsdc] = useState(20)
  const [minEth, setMinEth] = useState(0.002)
  const [reserveUsdcMin, setReserveUsdcMin] = useState(2)
  const [reserveUsdcWarn, setReserveUsdcWarn] = useState(2)
  const [reserveEthMin, setReserveEthMin] = useState(0.001)
  const [reserveEthWarn, setReserveEthWarn] = useState(0.0005)
  const fundingStatus = useAppStore(s => s.fundingStatus)
  const setFundingStatus = useAppStore(s => s.setFundingStatus)
  const addToast = useAppStore(s => s.addToast)

  useEffect(() => {
    if (serverError) setServerError('')
  }, [draftDisplayName])

  useEffect(() => {
    let mounted = true
    const loadFunding = async () => {
      if (bootstrapping) return
      try {
        const status: any = await api.getFundingStatus()
        if (!mounted) return
        setFundingStatus(status)
        if (typeof status?.required_usdc === 'number') setMinUsdc(status.required_usdc)
        else if (typeof status?.min_usdc === 'number') setMinUsdc(status.min_usdc)
        if (typeof status?.required_eth === 'number') setMinEth(status.required_eth)
        else if (typeof status?.min_eth === 'number') setMinEth(status.min_eth)
        if (typeof status?.fee_wallet_usdc_min === 'number') setReserveUsdcMin(status.fee_wallet_usdc_min)
        if (typeof status?.fee_wallet_usdc_min_warn === 'number') setReserveUsdcWarn(status.fee_wallet_usdc_min_warn)
        if (typeof status?.fee_wallet_eth_min === 'number') setReserveEthMin(status.fee_wallet_eth_min)
        if (typeof status?.fee_wallet_eth_min_warn === 'number') setReserveEthWarn(status.fee_wallet_eth_min_warn)
      } catch {}
    }
    if (!bootstrapping) {
      loadFunding()
    }
    // Poll faster when bridge is active
    const isBridging = fundingStatus?.funding_state === 'bridging' || fundingStatus?.funding_state === 'preparing'
    const intervalMs = isBridging ? 3000 : 10000
    const interval = setInterval(loadFunding, intervalMs)
    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [bootstrapping, fundingStatus?.funding_state])

  async function prefetchFunding() {
    try {
      const status: any = await api.getFundingStatus()
      setFundingStatus(status)
      if (typeof status?.suggested_usdc === 'number') setMinUsdc(status.suggested_usdc)
      else if (typeof status?.min_usdc === 'number') setMinUsdc(status.min_usdc)
      if (typeof status?.suggested_eth === 'number') setMinEth(status.suggested_eth)
      else if (typeof status?.min_eth === 'number') setMinEth(status.min_eth)
    } catch {}
  }

  async function handleWalletReady() {
    if (bootstrapping) return
    await Promise.race([
      prefetchFunding(),
      new Promise((resolve) => setTimeout(resolve, 1200)),
    ])
    setStep(2)
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
      onUpdateUser({ display_name: updated.display_name })
      setDraftDisplayName(updated.display_name || trimmed)
      setStep(3)
    } catch (e: any) {
      setServerError(e.message || 'Failed to save username')
    }
  }

  async function handleFixFunding() {
    try {
      const status: any = await api.forceFunding()
      setFundingStatus(status)
      addToast({ type: 'success', message: 'Fixing funding…' })
    } catch (e: any) {
      addToast({ type: 'error', message: e?.message ?? 'Funding fix failed' })
    }
  }

  function handleFinishOnboarding() {
    onUpdateUser({ onboarding_complete: true })
    setVisible(false)
    setTimeout(onDone, 0)
  }


  const displayNameNeedsSetup = !user.display_name || looksLikeAutoDisplayName(user.display_name)

  useEffect(() => {
    if (bootstrapping) {
      if (step !== 1) setStep(1)
      return
    }
    if (!displayNameNeedsSetup && step < 3) {
      setStep(3)
      return
    }
  }, [bootstrapping, displayNameNeedsSetup, step])

  if (!visible) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm px-4 py-6">
      <div className="w-full max-w-[420px] bg-[#111215] border border-white/[0.08] rounded-2xl p-5 sm:p-6 shadow-2xl flex flex-col max-h-[calc(100vh-3rem)] overflow-hidden">
        <div className="flex-1 flex flex-col overflow-y-auto min-h-0">
          {step === 1 && (
            <Step1Loading onDone={handleWalletReady} disabled={bootstrapping} />
          )}
          {step === 2 && (
            <Step2Username
              value={draftDisplayName}
              onChange={setDraftDisplayName}
              onDone={handleUsernameContinue}
              serverError={serverError}
            />
          )}
          {step === 3 && (
            <Step3Funding
              walletAddress={user.wallet_address || ''}
              minUsdc={minUsdc}
              minEth={minEth}
              walletFunded={Boolean(fundingStatus?.wallet_funded)}
              depositTxHash={fundingStatus?.funding_deposit_tx_hash || ''}
              usdcBalance={Number(fundingStatus?.usdc_balance || 0)}
              ethBalance={Number(fundingStatus?.eth_balance || 0)}
            requiredUsdc={Number(fundingStatus?.required_usdc || minUsdc)}
            requiredEth={Number(fundingStatus?.required_eth || minEth)}
            tradingBalance={Number(fundingStatus?.trading_balance || 0)}
            onboardingComplete={Boolean(fundingStatus?.onboarding_complete)}
            fundingState={fundingStatus?.funding_state || 'idle'}
            canFixFunding={Boolean(fundingStatus?.wallet_funded)}
            onFixFunding={handleFixFunding}
            bridgeTxHash={fundingStatus?.funding_tx_hash || ''}
            bridgeStartedAt={fundingStatus?.funding_bridge_started_at || ''}
            onFinish={handleFinishOnboarding}
            feeWalletUsdcMin={Number(fundingStatus?.fee_wallet_usdc_min || reserveUsdcMin)}
            feeWalletUsdcWarn={Number(fundingStatus?.fee_wallet_usdc_min_warn || reserveUsdcWarn)}
            feeWalletEthMin={Number(fundingStatus?.fee_wallet_eth_min || reserveEthMin)}
            feeWalletEthWarn={Number(fundingStatus?.fee_wallet_eth_min_warn || reserveEthWarn)}
          />
        )}
        </div>

        <StepDots current={step} />
      </div>
    </div>
  )
}
