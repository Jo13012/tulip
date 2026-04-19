/**
 * Admin Users — list all users with stats, inline fee override edit, session stop controls.
 */
import { useEffect, useRef, useState } from 'react'
import type { AdminUser } from '../../types'
import { api } from '../../utils/api'
import { fmtStoredBps, fmtStoredBpsPct, fmtUsd } from '../../utils/format'
import { useAppStore } from '../../store'

function truncAddr(addr: string | null): string {
  if (!addr) return '—'
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`
}

function fmtEth(v: number | null): string {
  if (v == null) return '—'
  return v.toFixed(5)
}

function fmtUsdc(v: number | null): string {
  if (v == null) return '—'
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function StatusDot({ live, status }: { live: boolean; status: string | null }) {
  if (!status) return <span className="w-1.5 h-1.5 rounded-full bg-white/15 inline-block" />
  const color = live && status === 'running'
    ? 'bg-emerald-400'
    : status === 'starting' || status === 'recovering'
      ? 'bg-yellow-400 animate-pulse'
      : 'bg-white/20'
  return <span className={`w-1.5 h-1.5 rounded-full inline-block ${color}`} />
}

/**
 * Inline cell for fee_override_bps.
 * Shows the override value if set (highlighted), or "—" if no override (using XP fee).
 * Click to edit: enter bps value to set override, or clear input to remove override.
 */
function FeeOverrideCell({ user, onUpdated }: {
  user: AdminUser
  onUpdated: (id: string, override: number | null) => void
}) {
  // UI works in real bps (e.g. 4 = 4 bps = 0.04%). DB stores bps×10 (40).
  const displayVal = user.fee_override_bps != null
    ? String((user.fee_override_bps / 10).toFixed(1).replace(/\.0$/, ''))
    : ''
  const [editing, setEditing] = useState(false)
  const [val, setVal]         = useState(displayVal)
  const [saving, setSaving]   = useState(false)
  const addToast = useAppStore(s => s.addToast)

  async function save() {
    const trimmed = val.trim()
    // Empty = clear override
    if (trimmed === '') {
      setSaving(true)
      try {
        await api.admin.updateUser(user.id, { fee_override_bps: -1 })
        onUpdated(user.id, null)
        addToast({ type: 'success', message: 'Override cleared — using XP fee' })
      } catch (e: any) {
        addToast({ type: 'error', message: e.message })
        setVal(displayVal)
      } finally {
        setSaving(false)
        setEditing(false)
      }
      return
    }
    const realBps = parseFloat(trimmed)
    if (isNaN(realBps) || realBps < 0 || realBps > 1000) {
      setVal(displayVal)
      setEditing(false)
      return
    }
    // Convert real bps → bps×10 for DB
    const storedBps = Math.round(realBps * 10)
    setSaving(true)
    try {
      await api.admin.updateUser(user.id, { fee_override_bps: storedBps })
      onUpdated(user.id, storedBps)
      addToast({ type: 'success', message: `Fee override → ${fmtStoredBps(storedBps)} (${fmtStoredBpsPct(storedBps)})` })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
      setVal(displayVal)
    } finally {
      setSaving(false)
      setEditing(false)
    }
  }

  if (editing) {
    return (
      <input
        className="w-16 bg-white/10 border border-white/20 rounded px-1.5 py-0.5 text-xs font-mono text-white focus:outline-none focus:border-brand"
        value={val}
        autoFocus
        placeholder="bps"
        onChange={e => setVal(e.target.value)}
        onBlur={save}
        onKeyDown={e => {
          if (e.key === 'Enter') save()
          if (e.key === 'Escape') { setVal(displayVal); setEditing(false) }
        }}
        disabled={saving}
      />
    )
  }

  const hasOverride = user.fee_override_bps != null
  return (
    <button
      className={`text-xs font-mono transition-colors cursor-pointer ${hasOverride ? 'text-amber-400 hover:text-amber-300' : 'text-white/25 hover:text-white/50'}`}
      onClick={() => { setVal(hasOverride ? displayVal : ''); setEditing(true) }}
      title={hasOverride ? 'Override active — click to edit or clear' : 'Click to set override'}
    >
      {hasOverride ? fmtStoredBps(user.fee_override_bps as number) : '—'}
    </button>
  )
}

const BALANCE_POLL_MS = 30_000

export default function AdminUsers() {
  const [users, setUsers]         = useState<AdminUser[]>([])
  const [loading, setLoading]     = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [stopping, setStopping]   = useState<string | null>(null)
  const addToast = useAppStore(s => s.addToast)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function fetchUsers(opts?: { silent?: boolean }) {
    if (!opts?.silent) setRefreshing(true)
    try {
      const d: any = await api.admin.listUsers()
      setUsers(d)
    } catch {}
    finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    fetchUsers()
    pollRef.current = setInterval(() => fetchUsers({ silent: true }), BALANCE_POLL_MS)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  function handleFeeUpdated(id: string, override: number | null) {
    setUsers(prev => prev.map(u => u.id === id ? { ...u, fee_override_bps: override } : u))
  }

  async function handleStop(userId: string, mode: 'soft' | 'hard') {
    setStopping(userId + mode)
    try {
      await api.admin.stopUserSession(userId, mode)
      addToast({ type: 'info', message: `Session ${mode} stop initiated` })
      await fetchUsers({ silent: true })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally {
      setStopping(null)
    }
  }

  if (loading) return <div className="p-6 text-white/30 text-xs">Loading…</div>

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold">Users</h1>
        <div className="flex items-center gap-3">
          {refreshing && <span className="text-[10px] text-white/25 animate-pulse">refreshing…</span>}
          <span className="text-xs text-white/25">{users.length} total</span>
          <button
            className="text-[10px] text-white/30 hover:text-white/60 transition-colors"
            onClick={() => fetchUsers()}
            disabled={refreshing}
            title="Refresh balances"
          >
            ↻
          </button>
        </div>
      </div>

      <div className="panel overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-white/[0.06]">
              {['', 'Name', 'Wallet', 'USDC', 'ETH', 'Trading Bal', 'Override fee', 'XP fee', 'Trading Vol', 'Bot PnL', 'Session', ''].map((h, i) => (
                <th key={i} className="text-left px-3 py-2 text-[10px] text-white/25 font-semibold uppercase tracking-wider">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                {/* Status dot */}
                <td className="px-3 py-2.5">
                  <StatusDot live={u.session_live} status={u.session_status} />
                </td>

                {/* Name */}
                <td className="px-3 py-2.5 text-white/70 max-w-[140px] truncate">
                  {u.display_name || <span className="text-white/20">—</span>}
                  {u.is_admin && (
                    <span className="ml-1 text-[9px] text-brand/70 font-semibold">ADMIN</span>
                  )}
                </td>

                {/* Wallet */}
                <td className="px-3 py-2.5 font-mono text-white/40">
                  {truncAddr(u.wallet_address)}
                </td>

                {/* Wallet USDC */}
                <td className={`px-3 py-2.5 font-mono text-xs ${
                  u.wallet_usdc == null ? 'text-white/25'
                  : u.wallet_usdc < 2   ? 'text-red-400'
                  : u.wallet_usdc < 10  ? 'text-yellow-400'
                  : 'text-emerald-400'
                }`}>
                  {fmtUsdc(u.wallet_usdc)}
                </td>

                {/* Wallet ETH */}
                <td className={`px-3 py-2.5 font-mono text-xs ${
                  u.wallet_eth == null    ? 'text-white/25'
                  : u.wallet_eth < 0.001 ? 'text-red-400'
                  : u.wallet_eth < 0.002 ? 'text-yellow-400'
                  : 'text-emerald-400'
                }`}>
                  {fmtEth(u.wallet_eth)}
                </td>

                {/* Trading balance */}
                <td className={`px-3 py-2.5 font-mono text-xs ${
                  u.trading_balance == null ? 'text-white/25'
                  : u.trading_balance < 20  ? 'text-red-400'
                  : u.trading_balance < 80  ? 'text-yellow-400'
                  : 'text-emerald-400'
                }`}>
                  {fmtUsdc(u.trading_balance)}
                </td>

                {/* Fee override (editable) */}
                <td className="px-3 py-2.5">
                  <FeeOverrideCell user={u} onUpdated={handleFeeUpdated} />
                </td>

                {/* XP-computed fee */}
                <td className="px-3 py-2.5 font-mono text-white/40">
                  {fmtStoredBps(u.effective_fee_bps)}
                </td>

                {/* Trading volume */}
                <td className="px-3 py-2.5 font-mono text-white/70">
                  {fmtUsd(u.trading_volume, 0)}
                </td>

                {/* Bot PnL */}
                <td className={`px-3 py-2.5 font-mono ${u.bot_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {fmtUsd(u.bot_pnl)}
                </td>

                {/* Session status */}
                <td className="px-3 py-2.5 text-white/30">
                  {u.session_status ? (
                    <span className={`font-mono ${u.session_live ? 'text-emerald-400/70' : 'text-white/30'}`}>
                      {u.session_status}
                    </span>
                  ) : '—'}
                </td>

                {/* Stop controls */}
                <td className="px-3 py-2.5">
                  {u.active_session_id && (
                    <div className="flex items-center gap-1.5">
                      <button
                        className="text-[10px] px-2 py-0.5 rounded bg-white/[0.06] text-white/40 hover:text-white/70 hover:bg-white/10 transition-colors disabled:opacity-30"
                        onClick={() => handleStop(u.id, 'soft')}
                        disabled={stopping === u.id + 'soft'}
                      >
                        soft stop
                      </button>
                      <button
                        className="text-[10px] px-2 py-0.5 rounded bg-red-500/10 text-red-400/60 hover:text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-30"
                        onClick={() => handleStop(u.id, 'hard')}
                        disabled={stopping === u.id + 'hard'}
                      >
                        hard stop
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {users.length === 0 && (
          <div className="text-center py-8 text-white/20 text-xs">No users found.</div>
        )}
      </div>
    </div>
  )
}
