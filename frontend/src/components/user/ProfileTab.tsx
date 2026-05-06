import { useEffect, useState } from 'react'
import { usePrivy } from '@privy-io/react-auth'
import type { AppUser, ProfileData } from '../../types'
import { useAppStore } from '../../store'
import { api } from '../../utils/api'
import { pnlColor, fmtStoredBps, fmtStoredBpsPct, fmtUsd } from '../../utils/format'
import { XPModal } from './XPModal'
import { BridgeHistoryModal } from './BridgeHistoryModal'
import { FundingModal } from './OnboardingModal'
import { Section, Row, AddressField, GhostButton } from '../ui/Field'

interface Props { user: AppUser }

const CACHE_KEY = (uid: string) => `profile_cache_${uid}`
const CACHE_TTL = 60_000

function readCache(uid: string): ProfileData | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY(uid))
    if (!raw) return null
    const { ts, data } = JSON.parse(raw)
    if (Date.now() - ts < CACHE_TTL) return data
  } catch {}
  return null
}

function writeCache(uid: string, data: ProfileData) {
  try {
    localStorage.setItem(CACHE_KEY(uid), JSON.stringify({ ts: Date.now(), data }))
  } catch {}
}

export function ProfileTab({ user }: Props) {
  const addToast = useAppStore(s => s.addToast)
  const fundingStatus = useAppStore(s => s.fundingStatus)
  usePrivy()

  const cached = readCache(user.user_id)
  const [profile, setProfile] = useState<ProfileData | null>(cached)
  const [loading, setLoading] = useState(!cached)
  const [xpOpen, setXpOpen] = useState(false)
  const [bridgeHistoryOpen, setBridgeHistoryOpen] = useState(false)
  const [fundingModalOpen, setFundingModalOpen] = useState(false)
  const [forcingFunding, setForcingFunding] = useState(false)
  const [fundTab, setFundTab] = useState<'deposit' | 'withdraw'>('deposit')

  useEffect(() => {
    api.getProfile().then((d: any) => {
      setProfile(d)
      writeCache(user.user_id, d)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [user.user_id])

  async function handleForceFunding() {
    setForcingFunding(true)
    try {
      const status = await api.forceFunding() as any
      useAppStore.getState().setFundingStatus(status)
      addToast({ type: 'success', message: 'Fixing funding...' })
    } catch (e: any) {
      addToast({ type: 'error', message: e?.message ?? 'Funding fix failed' })
    } finally {
      setForcingFunding(false)
    }
  }

  const pnl = profile ? Math.max(profile.bot_pnl, profile.wallet_pnl) : 0
  const vol = profile?.trading_volume ?? 0
  const feeBps = profile?.fee_bps ?? 120
  const feeIsOverridden = profile?.fee_is_overridden ?? false
  const sh = loading && !profile
  const dots = <span className="text-white/20 font-mono">...</span>

  const addr = user.wallet_address || ''
  const tradingBal = fundingStatus?.trading_balance ?? null
  const walletUsdc = fundingStatus?.usdc_balance ?? null
  const walletEth = fundingStatus?.eth_balance ?? null
  const bridgeAvailable = Boolean(fundingStatus?.bridge_available)
  const bridgeableUsdc = Number(fundingStatus?.bridgeable_usdc || 0)
  const reserveUsdcRequired = Number(fundingStatus?.required_usdc || fundingStatus?.fee_wallet_usdc_min || 2)
  const needsAction = Boolean(fundingStatus?.needs_usdc || fundingStatus?.needs_eth || fundingStatus?.funding_state === 'failed')
  const isSuggested = Boolean((fundingStatus?.warn_usdc || fundingStatus?.warn_eth) && !needsAction)
  const isReady = !needsAction && !isSuggested && fundingStatus != null

  const statusLabel = needsAction ? 'Action needed' : isSuggested ? 'Top up suggested' : isReady ? 'Ready' : 'Checking...'
  const statusColor = needsAction
    ? 'text-amber-300/80 bg-amber-500/10 border-amber-400/20'
    : isReady
      ? 'text-emerald-300/80 bg-emerald-500/10 border-emerald-400/20'
      : 'text-white/30 bg-white/[0.04] border-white/10'

  return (
    <div className="p-6 space-y-5">
      <p className="text-[18px] font-semibold text-white/80">
        Hi {user.display_name || 'Trader'}
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
        <div className="space-y-6">
          <Section title="Account">
            <Row label="Wallet">
              {addr
                ? <AddressField value={addr} />
                : <span className="text-[11px] text-white/20 font-mono">-</span>
              }
            </Row>
          </Section>

          <Section title="Performance">
            <Row label="PnL">
              <span className={`text-[12px] font-mono font-semibold tabular-nums ${sh ? 'text-white/20' : pnlColor(pnl)}`}>
                {sh ? dots : fmtUsd(pnl)}
              </span>
            </Row>

            <Row label="Trading Volume">
              <span className={`text-[12px] font-mono font-semibold tabular-nums ${sh ? 'text-white/20' : 'text-white/65'}`}>
                {sh ? dots : fmtUsd(vol, 0)}
              </span>
              <button
                onClick={() => setXpOpen(true)}
                disabled={sh}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/[0.05] hover:bg-white/[0.09] text-white/40 hover:text-white/70 transition-all text-[10px] font-semibold disabled:opacity-30"
              >
                <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
                  <rect x="1" y="1.5" width="10" height="1.5" rx=".75" fill="currentColor" opacity=".5"/>
                  <rect x="1" y="5.25" width="7" height="1.5" rx=".75" fill="currentColor" opacity=".7"/>
                  <rect x="1" y="9" width="4.5" height="1.5" rx=".75" fill="currentColor"/>
                </svg>
                View tier
              </button>
              {feeIsOverridden && (
                <span className="text-[10px] text-amber-400/60">custom fee</span>
              )}
            </Row>

            <Row label="Account Volume">
              <span className={`text-[12px] font-mono font-semibold tabular-nums ${sh ? 'text-white/20' : 'text-white/65'}`}>
                {sh ? dots : fmtUsd(profile?.wallet_volume ?? 0, 0)}
              </span>
            </Row>

            {feeIsOverridden && (
              <Row label="Fee rate">
                <span className="text-[12px] font-mono font-semibold text-amber-300/80">
                  {fmtStoredBps(feeBps)} ({fmtStoredBpsPct(feeBps)})
                </span>
                <span className="text-[9px] text-amber-400/50 border border-amber-400/20 bg-amber-500/5 px-1.5 py-0.5 rounded-full">
                  custom
                </span>
              </Row>
            )}
          </Section>
        </div>

        <Section
          title={(
            <div className="flex items-center gap-1">
              {(['deposit', 'withdraw'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setFundTab(t)}
                  className={`px-2.5 py-0.5 rounded-md text-[11px] font-semibold capitalize transition-colors ${
                    fundTab === t ? 'bg-white/[0.08] text-white/80' : 'text-white/30 hover:text-white/55'
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          )}
          action={fundTab === 'deposit' && fundingStatus ? (
            <span className={`text-[10px] font-semibold px-2.5 py-0.5 rounded-full border ${statusColor}`}>
              {statusLabel}
            </span>
          ) : undefined}
        >
          {fundTab === 'deposit' ? (
            <>
              <div className="px-4 py-3 space-y-3">
                {[
                  <>
                    Deposit <span className="text-white/65 font-semibold">USDC</span> to your wallet on{" "}
                    <span className="text-white/65 font-semibold">Arbitrum</span>.
                  </>,
                  <>
                    Keep a small amount of <span className="text-white/65 font-semibold">ETH</span> for gas fees{" "}
                    <span className="text-white/30">(around 0.0015 ETH)</span>.
                  </>,
                  <>
                    Your funds are automatically allocated to your trading balance for execution.
                  </>,
                  <>
                    A small safety buffer is maintained on your Arbitrum wallet: the greater of{" "}
                    <span className="text-white/65 font-semibold">${reserveUsdcRequired.toFixed(2)} USDC</span> or{" "}
                    <span className="text-white/65 font-semibold">
                      {Math.round((fundingStatus?.fee_buffer_pct ?? 0.05) * 100)}%
                    </span>{" "}
                    of your total capital.
                  </>,
                  <>
                    Any balance above{" "}
                    <span className="text-white/65 font-semibold">
                      ${(fundingStatus?.bridge_min_usdc ?? 5).toFixed(0)}+
                    </span>{" "}
                    is automatically bridged when available.
                  </>,
                ].map((text, i) => (
                  <div key={i} className="flex gap-3 items-start">
                    <span className="text-[10px] text-white/20 font-mono mt-0.5 w-3 flex-shrink-0">
                      {i + 1}
                    </span>
                    <p className="text-[11px] text-white/45 leading-relaxed">{text}</p>
                  </div>
                ))}
              </div>

              {addr && (
                <Row label="Deposit to">
                  <AddressField value={addr} />
                </Row>
              )}

              {(tradingBal != null || walletUsdc != null || walletEth != null) && (
                <>
                  {tradingBal != null && (
                    <Row label="Trading balance">
                      <span className="text-[12px] font-mono font-semibold text-white/65">
                        ${tradingBal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        {(fundingStatus?.positions_value ?? 0) > 0 && (
                          <span className="ml-1.5 text-[11px] font-normal text-white/35">
                            avail ${(tradingBal - (fundingStatus?.positions_value ?? 0)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                        )}
                      </span>
                    </Row>
                  )}
                  {(walletUsdc != null || walletEth != null) && (
                    <Row label="Wallet balances">
                      <span className="text-[12px] font-mono font-semibold text-white/65">
                        {walletUsdc != null
                          ? `$${walletUsdc.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDC`
                          : '-'}
                        {' · '}
                        {walletEth != null ? `${walletEth.toFixed(5)} ETH` : '-'}
                      </span>
                    </Row>
                  )}
                  {fundingStatus != null && (
                    <Row label="Bridge ready">
                      <span className="text-[12px] font-mono font-semibold text-white/65">
                        ${bridgeableUsdc.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} available
                      </span>
                    </Row>
                  )}
                </>
              )}

              <div className="px-4 py-3 space-y-2.5">
                {fundingStatus?.message && (
                  <p className={`text-[11px] ${needsAction ? 'text-amber-200/70' : 'text-white/30'}`}>
                    {fundingStatus.message}
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  <GhostButton onClick={() => setFundingModalOpen(true)}>
                    Bridge
                  </GhostButton>
                  {fundingStatus && !isReady && !(fundingStatus?.needs_usdc || fundingStatus?.needs_eth) && (
                    <GhostButton
                      disabled={forcingFunding || !bridgeAvailable || fundingStatus?.funding_state === 'bridging' || fundingStatus?.funding_state === 'preparing'}
                      onClick={handleForceFunding}
                    >
                      {(forcingFunding || fundingStatus?.funding_state === 'bridging' || fundingStatus?.funding_state === 'preparing') ? 'Bridging...' : 'Bridge to Polymarket'}
                    </GhostButton>
                  )}
                  <GhostButton onClick={() => setBridgeHistoryOpen(true)}>
                    History
                  </GhostButton>
                </div>
              </div>
            </>
          ) : (
            <div className="px-4 py-4 space-y-4">
              <div className="space-y-3">
                {[
                  <>Go to <span className="text-white/65 font-semibold">Settings</span> and export your private key.</>,
                  <>Import it into <span className="text-white/65 font-semibold">Rabby Wallet</span> or any compatible wallet.</>,
                  <>Connect to{' '}
                    <a
                      href="https://polymarket.com/?r=smurf"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-white/65 font-semibold underline underline-offset-2 hover:text-white/90 transition-colors"
                    >
                      polymarket.com/?r=smurf
                    </a>{' '}
                    with your wallet.</>,
                  <>Withdraw directly from your Polymarket account.</>,
                ].map((text, i) => (
                  <div key={i} className="flex gap-3 items-start">
                    <span className="text-[10px] text-white/20 font-mono mt-0.5 w-3 flex-shrink-0">{i + 1}</span>
                    <p className="text-[11px] text-white/45 leading-relaxed">{text}</p>
                  </div>
                ))}
              </div>

              <div className="flex items-start gap-2.5 px-3.5 py-3 rounded-xl border border-amber-400/15 bg-amber-500/[0.04]">
                <span className="text-[11px] text-amber-400/50 mt-0.5 flex-shrink-0">!</span>
                <p className="text-[10px] text-amber-300/55 leading-relaxed">
                  We do not store your private key and have no access to your funds at any time. You are the sole person with access to your wallet and remain responsible for its safekeeping.
                </p>
              </div>
            </div>
          )}
        </Section>
      </div>

      {xpOpen && (
        <XPModal
          trading_volume={vol}
          fee_bps={feeBps}
          fee_is_overridden={feeIsOverridden}
          onClose={() => setXpOpen(false)}
        />
      )}
      {bridgeHistoryOpen && (
        <BridgeHistoryModal onClose={() => setBridgeHistoryOpen(false)} />
      )}
      {fundingModalOpen && (
        <FundingModal
          user={user}
          onClose={() => setFundingModalOpen(false)}
        />
      )}
    </div>
  )
}
