import { useEffect, useState } from 'react'
import { useAppStore } from '../../store'
import { api } from '../../utils/api'
import { fmtUsd } from '../../utils/format'
import type { AppUser, ProfileData, ReferralListItem } from '../../types'
import { Section, Row, AddressField } from '../ui/Field'

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

export function ReferralTab({ user }: Props) {
  const addToast = useAppStore(s => s.addToast)
  const setUser = useAppStore(s => s.setUser)

  const cached = readCache(user.user_id)
  const [profile, setProfile] = useState<ProfileData | null>(cached)
  const [referrerInput, setReferrerInput] = useState(user.referrer_username || '')
  const [savingReferrer, setSavingReferrer] = useState(false)

  const [referrals, setReferrals] = useState<ReferralListItem[]>([])
  const [referralsLoading, setReferralsLoading] = useState(true)

  useEffect(() => {
    if (!cached) {
      api.getProfile().then((d: any) => {
        setProfile(d)
        writeCache(user.user_id, d)
      }).catch(() => {})
    }
    api.getReferrals()
      .then((data) => setReferrals(data ?? []))
      .catch(() => {})
      .finally(() => setReferralsLoading(false))
  }, [user.user_id])

  useEffect(() => {
    setReferrerInput(user.referrer_username || profile?.referrer_username || '')
  }, [user.referrer_username, profile?.referrer_username])

  async function handleSaveReferrer() {
    const username = referrerInput.trim()
    if (!username) {
      addToast({ type: 'error', message: 'Enter a referrer username' })
      return
    }
    setSavingReferrer(true)
    try {
      await api.lookupReferrer(username)
      const assigned = await api.assignReferrer(username) as any
      const nextReferrer = assigned.referrer_username || username
      const nextProfile: ProfileData = {
        trading_volume: profile?.trading_volume ?? 0,
        bot_pnl: profile?.bot_pnl ?? 0,
        fee_bps: profile?.fee_bps ?? 120,
        fee_is_overridden: profile?.fee_is_overridden ?? false,
        wallet_volume: profile?.wallet_volume ?? 0,
        wallet_pnl: profile?.wallet_pnl ?? 0,
        referrer_username: nextReferrer,
        can_set_referrer: false,
        referral_reward_usdc: profile?.referral_reward_usdc ?? 0,
      }
      setProfile(nextProfile)
      writeCache(user.user_id, nextProfile)
      setUser({ ...user, referrer_username: nextReferrer })
      setReferrerInput(nextReferrer)
      addToast({ type: 'success', message: 'Referrer saved' })
    } catch (e: any) {
      addToast({ type: 'warning', message: e?.message || 'Unable to set this referrer.' })
    } finally {
      setSavingReferrer(false)
    }
  }

  const resolvedReferrer = profile?.referrer_username || user.referrer_username || ''
  const canSetReferrer = Boolean(profile?.can_set_referrer && !resolvedReferrer)
  const referralLink = user.display_name ? `${window.location.origin}/?r=${encodeURIComponent(user.display_name)}` : ''

  return (
    <div className="p-6">
      <p className="text-[18px] font-semibold text-white/80 mb-5">Referral</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
        {/* Left: referral management */}
        <div className="space-y-6">
          <Section title="Referral">
            <Row label="Your referrer">
              {canSetReferrer ? (
                <>
                  <input
                    type="text"
                    value={referrerInput}
                    onChange={(e) => setReferrerInput(e.target.value.replace(/[^A-Za-z0-9]/g, ''))}
                    placeholder="Enter username"
                    maxLength={15}
                    className="w-full max-w-[220px] bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-[12px] text-white placeholder-white/20 outline-none transition-colors focus:border-white/20"
                  />
                  <button
                    disabled={savingReferrer || !referrerInput.trim()}
                    onClick={handleSaveReferrer}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/[0.05] hover:bg-white/[0.09] text-white/40 hover:text-white/70 transition-all text-[10px] font-semibold disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    {savingReferrer ? 'Saving...' : 'Set referrer'}
                  </button>
                </>
              ) : (
                <span className="text-[12px] font-mono font-semibold text-white/65">
                  {resolvedReferrer || <span className="text-white/20 font-mono">...</span>}
                </span>
              )}
              {canSetReferrer && (
                <p className="text-[11px] text-white/40 leading-relaxed">
                  Enter your referrer&apos;s username to link your account. Referrer can only be set once.
                </p>
              )}
            </Row>

            <Row label="Rewards">
              <span className="text-[12px] font-mono font-semibold text-emerald-400">
                {fmtUsd(profile?.referral_reward_usdc ?? user.referral_reward_usdc ?? 0)}
              </span>
            </Row>

            <Row label="Custom Link">
              {referralLink
                ? <AddressField value={referralLink} />
                : <span className="text-[11px] text-white/20 font-mono">Set your username first</span>
              }
            </Row>
          </Section>
        </div>

        {/* Right: referrals list inline */}
        <Section title="Your Referrals">
          <div className="grid grid-cols-[1fr_6.5rem_1.5rem] gap-x-4 px-5 py-2 border-b border-white/[0.04]">
            {['Username', 'Generated', ''].map((h, i) => (
              <span key={i} className={`text-[9px] uppercase tracking-widest text-white/20 font-semibold ${i === 1 ? 'text-right' : ''}`}>
                {h}
              </span>
            ))}
          </div>

          <div className="overflow-y-auto max-h-[360px]">
            {referralsLoading && (
              <div className="flex items-center justify-center py-10">
                <svg className="animate-spin w-5 h-5 text-white/30" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" strokeOpacity="0.2"/>
                  <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                </svg>
              </div>
            )}
            {!referralsLoading && referrals.length === 0 && (
              <p className="text-center text-white/20 text-xs py-10">No referrals yet.</p>
            )}
            {!referralsLoading && referrals.map((referral, i) => (
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
              Earn <span className="text-white/80">10%</span> of your referrals' trading fees.{" "}
              <span className="text-white/20">
                We reserve the right to withhold rewards in cases of abuse, including self-referrals, multi-accounting, or other unfair practices.
              </span>
            </div>
          </div>
        </Section>
      </div>
    </div>
  )
}
