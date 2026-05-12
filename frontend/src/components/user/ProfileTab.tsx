import { useEffect, useRef, useState } from 'react'
import { usePrivy } from '@privy-io/react-auth'
import { Copy, Check, ChevronDown } from 'lucide-react'
import type { AppUser, ProfileData, DepositConfigOut, DepositChain } from '../../types'
import { api } from '../../utils/api'
import { pnlColor, fmtUsd } from '../../utils/format'
import { XPModal } from './XPModal'
import { Section, Row, AddressField, GhostButton } from '../ui/Field'
import { CHAIN_ICON_META, TOKEN_ICON_MAP, TOKEN_MIN_FALLBACK, STATIC_DEPOSIT_CHAINS } from '../ui/ChainIcons'

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

const CHAIN_META = CHAIN_ICON_META
const TOKEN_ICONS = TOKEN_ICON_MAP

function ChainDropdown({
  chains,
  selectedId,
  onChange,
}: {
  chains: DepositChain[]
  selectedId: string
  onChange: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const selected = chains.find(c => c.chain_id === selectedId) ?? chains[0]
  const meta = CHAIN_META[selected?.chain_id] ?? CHAIN_META['42161']

  useEffect(() => {
    if (!open) return
    function h(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 px-3 py-2 rounded-xl border border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06] transition-all w-full"
      >
        <meta.Icon size={20} />
        <span className="flex-1 text-left text-[12px] font-semibold text-white/80">{meta.label}</span>
        <ChevronDown size={13} className={`text-white/30 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute top-full left-0 right-0 mt-1 rounded-xl border border-white/[0.08] bg-[#0f1012] shadow-2xl z-20 overflow-hidden">
          {chains.map(c => {
            const m = CHAIN_META[c.chain_id] ?? CHAIN_META['42161']
            const active = c.chain_id === selectedId
            return (
              <button
                key={c.chain_id}
                type="button"
                onClick={() => { onChange(c.chain_id); setOpen(false) }}
                className={`flex items-center gap-2.5 w-full px-3.5 py-2.5 transition-colors text-left ${
                  active ? 'bg-white/[0.06]' : 'hover:bg-white/[0.04]'
                }`}
              >
                <m.Icon size={18} />
                <span className={`text-[12px] font-semibold ${active ? 'text-white/90' : 'text-white/55'}`}>
                  {m.label}
                </span>
                {active && (
                  <span className="ml-auto w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0" />
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function DepositSection({ depositConfig }: { depositConfig: DepositConfigOut | null }) {
  const [selectedChainId, setSelectedChainId] = useState('42161')
  const [copiedAddr, setCopiedAddr] = useState(false)

  const chains = (depositConfig?.chains?.length ? depositConfig.chains : STATIC_DEPOSIT_CHAINS)
  const chain = chains.find(c => c.chain_id === selectedChainId) ?? chains[0]
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

  if (!depositConfig) {
    return (
      <div className="px-5 py-8 flex items-center gap-3">
        <div className="w-4 h-4 rounded-full border-2 border-white/10 border-t-white/30 animate-spin flex-shrink-0" />
        <span className="text-[11px] text-white/25">Loading deposit info…</span>
      </div>
    )
  }

  if (!depositConfig.account_ready) {
    return (
      <div className="px-5 py-8 text-center space-y-1.5">
        <p className="text-[12px] font-medium text-white/40">Account setup required</p>
        <p className="text-[11px] text-white/20">Complete onboarding to receive your deposit address.</p>
      </div>
    )
  }

  if (!depositConfig.deposit_addresses.evm && !depositConfig.deposit_addresses.svm) {
    return (
      <div className="px-5 py-8 text-center space-y-1.5">
        <p className="text-[12px] font-medium text-amber-400/70">Temporarily unavailable</p>
        <p className="text-[11px] text-white/25">Deposit addresses could not be fetched. Try again later.</p>
      </div>
    )
  }

  const totalBal = (depositConfig.trading_balance ?? 0) + depositConfig.positions_value
  const isFunded = depositConfig.is_funded_now
  const chainMeta = CHAIN_META[selectedChainId] ?? CHAIN_META['42161']

  return (
    <div className="p-4 space-y-3">

      {/* Balance row */}
      <div className="flex items-center justify-between rounded-xl bg-white/[0.03] border border-white/[0.05] px-4 py-3">
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-widest text-white/25 mb-0.5">Polymarket balance</p>
          <p className={`text-[17px] font-mono font-bold tabular-nums leading-none ${isFunded ? 'text-white/90' : 'text-white/25'}`}>
            {depositConfig.trading_balance != null
              ? `$${totalBal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : '—'}
          </p>
        </div>
        {depositConfig.trading_cash_available != null && depositConfig.positions_value > 0 && (
          <div className="text-right">
            <p className="text-[9px] font-semibold uppercase tracking-widest text-white/20 mb-0.5">Available</p>
            <p className="text-[13px] font-mono text-white/45 tabular-nums">
              ${(depositConfig.trading_cash_available ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </p>
          </div>
        )}
      </div>

      {/* Chain selector dropdown */}
      <ChainDropdown
        chains={chains}
        selectedId={selectedChainId}
        onChange={setSelectedChainId}
      />

      {/* Address block */}
      <div className="rounded-xl border overflow-hidden" style={{ borderColor: chainMeta.borderColor }}>
        {/* Header */}
        <div className="flex items-center justify-between px-3.5 py-2.5" style={{ background: `${chainMeta.color}08` }}>
          <div className="flex items-center gap-2">
            <chainMeta.Icon size={14} />
            <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: `${chainMeta.color}99` }}>
              {chainMeta.label} · deposit address
            </span>
          </div>
          <button
            type="button"
            onClick={copyAddr}
            disabled={!depositAddr}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-semibold transition-all ${
              copiedAddr
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/20'
                : 'bg-white/[0.05] text-white/40 hover:bg-white/[0.09] hover:text-white/70 border border-white/[0.07]'
            }`}
          >
            {copiedAddr ? <><Check size={10} strokeWidth={2.5} />Copied</> : <><Copy size={10} />Copy</>}
          </button>
        </div>

        {/* Address */}
        <div className="px-3.5 py-3 bg-[#070809]">
          {depositAddr
            ? <p className="font-mono text-[11px] text-white/60 break-all leading-relaxed select-all cursor-text">{depositAddr}</p>
            : <p className="text-[11px] text-white/20 font-mono">—</p>
          }
        </div>

        {/* Token rows */}
        {chain && chain.tokens.length > 0 && (
          <div className="border-t border-white/[0.05] divide-y divide-white/[0.03]">
            <div className="grid grid-cols-[1fr_auto] gap-2 px-3.5 py-1.5">
              <span className="text-[9px] uppercase tracking-widest text-white/20 font-semibold">Token</span>
              <span className="text-[9px] uppercase tracking-widest text-white/20 font-semibold text-right">Min deposit</span>
            </div>
            {chain.tokens.map(tok => {
              const TokIcon = TOKEN_ICONS[tok.symbol]
              return (
                <div key={tok.symbol} className="grid grid-cols-[1fr_auto] gap-2 items-center px-3.5 py-2.5 hover:bg-white/[0.02] transition-colors">
                  <div className="flex items-center gap-2">
                    {TokIcon
                      ? <TokIcon size={18} />
                      : <span className="w-[18px] h-[18px] rounded-full bg-white/10 flex items-center justify-center text-[8px] text-white/40 font-bold">{tok.symbol[0]}</span>
                    }
                    <span className="text-[12px] font-semibold text-white/80">{tok.symbol}</span>
                  </div>
                  <span className="text-[11px] font-mono text-white/40 text-right">
                    {tok.min_checkout_usd != null ? `$${tok.min_checkout_usd}` : (TOKEN_MIN_FALLBACK[tok.symbol] ?? '—')}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      <p className="text-[10px] text-white/20 text-center pt-0.5">
        Funds credited directly on Polymarket · No bridge · ~2 min
      </p>

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
