import { useEffect, useState } from 'react'
import { usePrivy } from '@privy-io/react-auth'
import { Copy, Check } from 'lucide-react'
import type { AppUser, ProfileData, DepositConfigOut } from '../../types'
import { api } from '../../utils/api'
import { pnlColor, fmtUsd } from '../../utils/format'
import { XPModal } from './XPModal'
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

const CHAIN_LABELS: Record<string, string> = {
  '42161': 'Arbitrum',
  '8453':  'Base',
  'solana': 'Solana',
}

function DepositSection({ depositConfig }: { depositConfig: DepositConfigOut | null }) {
  const [selectedChainId, setSelectedChainId] = useState('42161')
  const [copied, setCopied] = useState(false)

  const chain = depositConfig?.chains.find(c => c.chain_id === selectedChainId)
    ?? depositConfig?.chains[0]
  const depositAddr = chain?.address_type === 'svm'
    ? (depositConfig?.deposit_addresses.svm ?? '')
    : (depositConfig?.deposit_addresses.evm ?? '')

  function copyAddr() {
    if (!depositAddr) return
    navigator.clipboard.writeText(depositAddr).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (!depositConfig) {
    return (
      <div className="px-4 py-4 text-[11px] text-white/30">
        Loading deposit info…
      </div>
    )
  }

  if (!depositConfig.account_ready) {
    return (
      <div className="px-4 py-4 text-[11px] text-white/30">
        Complete account setup to get your deposit address.
      </div>
    )
  }

  if (!depositConfig.deposit_addresses.evm && !depositConfig.deposit_addresses.svm) {
    return (
      <div className="px-4 py-4 text-[11px] text-amber-400/60">
        Deposit temporarily unavailable. Try again later.
      </div>
    )
  }

  const totalBal = (depositConfig.trading_balance ?? 0) + depositConfig.positions_value
  const isFunded = depositConfig.is_funded_now

  return (
    <div className="space-y-1">
      {/* Chain tabs */}
      <div className="px-4 pt-3 flex gap-2">
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

      {/* Address */}
      <Row label="Deposit to">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <div className="flex-1 min-w-0">
            {depositAddr
              ? <AddressField value={depositAddr} />
              : <span className="text-[11px] text-white/20 font-mono">—</span>}
          </div>
          {depositAddr && (
            <button
              type="button"
              onClick={copyAddr}
              className="flex-shrink-0 text-white/25 hover:text-white/60 transition-colors"
            >
              {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
            </button>
          )}
        </div>
      </Row>

      {/* Accepted tokens */}
      {chain && chain.tokens.length > 0 && (
        <div className="px-4 pb-2 flex gap-3">
          {chain.tokens.map(tok => (
            <span key={tok.symbol} className="text-[10px] text-white/30">
              {tok.symbol}
              {tok.min_checkout_usd != null && (
                <span className="text-white/20"> (min ${tok.min_checkout_usd})</span>
              )}
            </span>
          ))}
        </div>
      )}

      {/* Trading balance */}
      <Row label="Trading balance">
        <span className={`text-[12px] font-mono font-semibold ${isFunded ? 'text-white/65' : 'text-white/25'}`}>
          {depositConfig.trading_balance != null
            ? `$${totalBal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : '—'}
        </span>
      </Row>

      {/* Info note */}
      <div className="px-4 pb-3 pt-1">
        <p className="text-[10px] text-white/25 leading-relaxed">
          Deposited funds are credited directly on Polymarket. No bridge needed. Appears within a few minutes.
        </p>
      </div>
    </div>
  )
}

export function ProfileTab({ user }: Props) {
  usePrivy()

  const cached = readCache(user.user_id)
  const [profile, setProfile] = useState<ProfileData | null>(cached)
  const [loading, setLoading] = useState(!cached)
  const [xpOpen, setXpOpen] = useState(false)
  const [fundTab, setFundTab] = useState<'deposit' | 'withdraw'>('deposit')
  const [depositConfig, setDepositConfig] = useState<DepositConfigOut | null>(null)

  useEffect(() => {
    api.getProfile().then((d: any) => {
      setProfile(d)
      writeCache(user.user_id, d)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [user.user_id])

  useEffect(() => {
    let mounted = true
    const load = async () => {
      try {
        const cfg = await api.getDepositConfig()
        if (mounted) setDepositConfig(cfg)
      } catch {}
    }
    load()
    const iv = setInterval(load, 15000)
    return () => { mounted = false; clearInterval(iv) }
  }, [])

  const pnl = profile ? Math.max(profile.bot_pnl, profile.wallet_pnl) : 0
  const vol = profile?.trading_volume ?? 0
  const feeBps = profile?.fee_bps ?? 120
  const feeIsOverridden = profile?.fee_is_overridden ?? false
  const sh = loading && !profile
  const dots = <span className="text-white/20 font-mono">...</span>

  const addr = user.wallet_address || ''

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
        >
          {fundTab === 'deposit' ? (
            <DepositSection depositConfig={depositConfig} />
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
                  We do not store your private key and have no access to your funds at any time.
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
    </div>
  )
}
