/**
 * Admin Users — list all users with stats, inline fee override edit, session stop controls.
 */
import { useEffect, useRef, useState } from 'react'
import type { AdminUser } from '../../types'
import { api } from '../../utils/api'
import { fmtStoredBps, fmtStoredBpsPct, fmtUsd } from '../../utils/format'
import { useAppStore } from '../../store'

// ── Nuclear Close Modal ───────────────────────────────────────────────────────

interface PolyOrder {
  order_id: string
  token_id: string
  question: string
  side: string
  price: number
  size_remaining: number
}
interface PolyPosition {
  token_id: string
  question: string
  balance: number
  mid: number
  value_usd: number
}

function NuclearModal({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const [loading, setLoading]           = useState(true)
  const [executing, setExecuting]       = useState(false)
  const [orders, setOrders]             = useState<PolyOrder[]>([])
  const [positions, setPositions]       = useState<PolyPosition[]>([])
  const [totalValueUsd, setTotalValueUsd] = useState(0)
  const [selectedOrders, setSelectedOrders] = useState<Set<string>>(new Set())
  const [selectedTokens, setSelectedTokens] = useState<Set<string>>(new Set())
  const [result, setResult]             = useState<any>(null)
  const addToast = useAppStore(s => s.addToast)

  useEffect(() => {
    ;(async () => {
      try {
        const d: any = await api.admin.getUserPolyState(user.id)
        setOrders(d.open_orders || [])
        setPositions(d.positions || [])
        setTotalValueUsd(d.total_value_usd || 0)
        setSelectedOrders(new Set((d.open_orders || []).map((o: PolyOrder) => o.order_id)))
        setSelectedTokens(new Set((d.positions || []).map((p: PolyPosition) => p.token_id)))
      } catch (e: any) {
        addToast({ type: 'error', message: e.message })
        onClose()
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  function toggleToken(tid: string) {
    setSelectedTokens(prev => {
      const next = new Set(prev)
      next.has(tid) ? next.delete(tid) : next.add(tid)
      return next
    })
  }

  function toggleOrder(oid: string) {
    setSelectedOrders(prev => {
      const next = new Set(prev)
      next.has(oid) ? next.delete(oid) : next.add(oid)
      return next
    })
  }

  function toggleAllOrders() {
    setSelectedOrders(prev =>
      prev.size === orders.length ? new Set() : new Set(orders.map(o => o.order_id))
    )
  }

  async function execute() {
    if (selectedOrders.size === 0 && selectedTokens.size === 0) return
    setExecuting(true)
    try {
      const res: any = await api.admin.nuclearClose(user.id, {
        cancel_orders: false,
        cancel_order_ids: Array.from(selectedOrders),
        sell_token_ids: Array.from(selectedTokens),
      })
      setResult(res)
      addToast({ type: 'success', message: `Done: ${res.cancelled_orders?.length ?? 0} cancelled, ${res.sold_positions?.length ?? 0} sold` })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally {
      setExecuting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70" onClick={onClose}>
      <div
        className="bg-[#0d0e10] border border-white/[0.08] rounded-2xl w-full max-w-lg mx-4 p-5 space-y-4 max-h-[80vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-red-400">Nuclear Close</p>
            <p className="text-[10px] text-white/30">{user.display_name || user.id}</p>
          </div>
          <button className="text-white/30 hover:text-white/70 text-sm" onClick={onClose}>✕</button>
        </div>

        {loading ? (
          <p className="text-[11px] text-white/30 text-center py-6">Fetching Poly state…</p>
        ) : result ? (
          <div className="space-y-2">
            <p className="text-[11px] text-emerald-400 font-semibold">Executed</p>
            <p className="text-[10px] text-white/50">Cancelled: {result.cancelled_orders?.length ?? 0} orders</p>
            {result.sold_positions?.length > 0 && (
              <div className="space-y-0.5">
                <p className="text-[10px] text-white/50">
                  Sold: {result.sold_positions.length} positions
                  {' '}(~${result.sold_positions.reduce((s: number, p: any) => s + (p.estimated_usdc ?? 0), 0).toFixed(2)})
                </p>
                {result.sold_positions.map((p: any, i: number) => (
                  <p key={i} className="text-[10px] text-white/30 font-mono pl-2">
                    {p.token_id?.slice(0, 14)}… {p.tokens?.toFixed(1)} tok @ {((p.price ?? 0) * 100).toFixed(1)}¢ = ~${(p.estimated_usdc ?? 0).toFixed(2)}
                  </p>
                ))}
              </div>
            )}
            {result.redeemed_positions?.length > 0 && (
              <div className="space-y-0.5">
                <p className="text-[10px] text-emerald-400/80">
                  Redeemed: {result.redeemed_positions.length} positions
                  {' '}(~${result.redeemed_positions.reduce((s: number, p: any) => s + (p.estimated_usdc ?? 0), 0).toFixed(2)} pUSD)
                </p>
                {result.redeemed_positions.map((p: any, i: number) => (
                  <p key={i} className={`text-[10px] font-mono pl-2 ${p.ok ? 'text-emerald-400/60' : 'text-yellow-400/60'}`}>
                    {p.token_id?.slice(0, 14)}… ~${(p.estimated_usdc ?? 0).toFixed(2)} {p.ok ? '✓' : `⚠ ${p.error ?? 'failed'}`}
                  </p>
                ))}
              </div>
            )}
            {result.errors?.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] text-red-400">Errors:</p>
                {result.errors.map((e: string, i: number) => (
                  <p key={i} className="text-[10px] text-red-400/70 font-mono break-all">{e}</p>
                ))}
              </div>
            )}
            <button className="mt-2 text-[11px] text-white/40 hover:text-white/70" onClick={onClose}>Close</button>
          </div>
        ) : (
          <>
            {/* Open orders — individual rows with checkboxes */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <p className="text-[10px] text-white/30 font-semibold uppercase tracking-wider">Open orders to cancel</p>
                {orders.length > 0 && (
                  <button className="text-[10px] text-white/30 hover:text-white/60" onClick={toggleAllOrders}>
                    {selectedOrders.size === orders.length ? 'Deselect all' : 'Select all'}
                  </button>
                )}
              </div>
              {orders.length === 0 ? (
                <p className="text-[10px] text-white/20 px-1">No open orders on Poly</p>
              ) : orders.map(ord => (
                <div
                  key={ord.order_id}
                  className="flex items-center justify-between px-3 py-2 rounded-xl border border-white/[0.07] bg-white/[0.02] cursor-pointer"
                  onClick={() => toggleOrder(ord.order_id)}
                >
                  <div className="flex-1 min-w-0 pr-3">
                    <p className="text-[11px] text-white/70 leading-snug truncate">
                      {ord.question || ord.token_id.slice(0, 24) + '…'}
                    </p>
                    <p className="text-[10px] text-white/30 mt-0.5 font-mono">
                      {ord.side} · {(ord.price * 100).toFixed(1)}¢ · {ord.size_remaining.toFixed(2)} tok
                    </p>
                  </div>
                  <div className={`flex-shrink-0 w-4 h-4 rounded border flex items-center justify-center transition-colors ${selectedOrders.has(ord.order_id) ? 'bg-red-500/80 border-red-500' : 'border-white/20'}`}>
                    {selectedOrders.has(ord.order_id) && <span className="text-[9px] text-white font-bold">✓</span>}
                  </div>
                </div>
              ))}
            </div>

            {/* Positions */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <p className="text-[10px] text-white/30 font-semibold uppercase tracking-wider">Positions to sell (taker)</p>
                {positions.length > 0 && (
                  <p className="text-[11px] font-semibold text-white/60">
                    Total: <span className="text-white/80">${totalValueUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </p>
                )}
              </div>
              {positions.length === 0 ? (
                <p className="text-[10px] text-white/20 px-1">No token positions found on Poly</p>
              ) : positions.map(pos => (
                <div
                  key={pos.token_id}
                  className="flex items-center justify-between px-3 py-2 rounded-xl border border-white/[0.07] bg-white/[0.02] cursor-pointer"
                  onClick={() => toggleToken(pos.token_id)}
                >
                  <div className="flex-1 min-w-0 pr-3">
                    <p className="text-[11px] text-white/70 leading-snug truncate">
                      {pos.question || pos.token_id.slice(0, 24) + '…'}
                    </p>
                    <p className="text-[10px] text-white/30 mt-0.5">
                      {pos.balance.toFixed(2)} tok
                      {pos.mid > 0 && <> · <span className="text-white/50">${pos.value_usd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span> @ {(pos.mid * 100).toFixed(1)}¢</>}
                    </p>
                  </div>
                  <div className={`flex-shrink-0 w-4 h-4 rounded border flex items-center justify-center transition-colors ${selectedTokens.has(pos.token_id) ? 'bg-red-500/80 border-red-500' : 'border-white/20'}`}>
                    {selectedTokens.has(pos.token_id) && <span className="text-[9px] text-white font-bold">✓</span>}
                  </div>
                </div>
              ))}
            </div>

            {/* Warning + confirm */}
            <div className="px-3 py-2.5 rounded-xl bg-red-500/5 border border-red-500/20">
              <p className="text-[10px] text-red-400/80">Executes directly on Polymarket. Sell orders placed at best bid (taker). Cannot be undone.</p>
            </div>

            <button
              className="w-full py-2.5 rounded-xl bg-red-500/20 border border-red-500/30 text-red-400 text-[11px] font-semibold hover:bg-red-500/30 transition-colors disabled:opacity-40"
              onClick={execute}
              disabled={executing || (selectedOrders.size === 0 && selectedTokens.size === 0)}
            >
              {executing ? 'Executing…' : 'Execute Nuclear Close'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

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
  const freeDaysLeft = (() => {
    if (!user.free_fee_ends_at) return 0
    const ms = new Date(user.free_fee_ends_at).getTime() - Date.now()
    return ms > 0 ? Math.ceil(ms / 86_400_000) : 0
  })()
  const isFreeFee = user.fee_override_bps === 0 && freeDaysLeft > 0

  return (
    <div className="flex items-center gap-1">
      <button
        className={`text-xs font-mono transition-colors cursor-pointer ${hasOverride ? (isFreeFee ? 'text-emerald-400 hover:text-emerald-300' : 'text-amber-400 hover:text-amber-300') : 'text-white/25 hover:text-white/50'}`}
        onClick={() => { setVal(hasOverride ? displayVal : ''); setEditing(true) }}
        title={hasOverride ? 'Override active — click to edit or clear' : 'Click to set override'}
      >
        {hasOverride ? fmtStoredBps(user.fee_override_bps as number) : '—'}
      </button>
      {isFreeFee && (
        <span className="text-[9px] text-emerald-400/60 font-mono">{freeDaysLeft}d</span>
      )}
    </div>
  )
}

function GrantFreeFeeCell({ user, onUpdated }: {
  user: AdminUser
  onUpdated: (id: string, patch: Partial<AdminUser>) => void
}) {
  const [editing, setEditing] = useState(false)
  const [days, setDays] = useState('7')
  const [saving, setSaving] = useState(false)

  const freeDaysLeft = (() => {
    if (!user.free_fee_ends_at) return 0
    const ms = new Date(user.free_fee_ends_at).getTime() - Date.now()
    return ms > 0 ? Math.ceil(ms / 86_400_000) : 0
  })()
  const isActive = user.fee_override_bps === 0 && freeDaysLeft > 0

  const grant = async () => {
    const n = parseInt(days)
    if (!n || n < 1) return
    setSaving(true)
    try {
      await api.admin.updateUser(user.id, { free_fee_days: n })
      const ends = new Date(Date.now() + n * 86_400_000).toISOString()
      onUpdated(user.id, { fee_override_bps: 0, free_fee_ends_at: ends })
      setEditing(false)
    } finally { setSaving(false) }
  }

  const cancel = async () => {
    setSaving(true)
    try {
      await api.admin.updateUser(user.id, { free_fee_days: -1 })
      onUpdated(user.id, { fee_override_bps: null, free_fee_ends_at: null })
    } finally { setSaving(false) }
  }

  if (editing) return (
    <div className="flex items-center gap-1">
      <input
        className="w-10 text-xs font-mono bg-white/5 border border-white/10 rounded px-1 py-0.5 text-white/80"
        value={days} onChange={e => setDays(e.target.value)} type="number" min={1} max={365}
        autoFocus onKeyDown={e => { if (e.key === 'Enter') grant(); if (e.key === 'Escape') setEditing(false) }}
      />
      <span className="text-[9px] text-white/30">d</span>
      <button className="text-[9px] text-emerald-400 hover:text-emerald-300" onClick={grant} disabled={saving}>✓</button>
      <button className="text-[9px] text-white/30 hover:text-white/60" onClick={() => setEditing(false)}>✕</button>
    </div>
  )

  if (isActive) return (
    <div className="flex items-center gap-1">
      <span className="text-[10px] text-emerald-400/80 font-mono">{freeDaysLeft}d left</span>
      <button className="text-[9px] text-white/20 hover:text-red-400" onClick={cancel} disabled={saving} title="Cancel free fee">✕</button>
    </div>
  )

  return (
    <button
      className="text-[9px] text-white/20 hover:text-emerald-400 transition-colors"
      onClick={() => setEditing(true)}
      title="Grant free fee period"
    >+ free</button>
  )
}

const BALANCE_POLL_MS = 30_000

export default function AdminUsers() {
  const [users, setUsers]         = useState<AdminUser[]>([])
  const [loading, setLoading]     = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [stopping, setStopping]       = useState<string | null>(null)
  const [nuclearUser, setNuclearUser] = useState<AdminUser | null>(null)
  const [onboardedOnly, setOnboardedOnly] = useState(true)
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
  function handleUserPatched(id: string, patch: Partial<AdminUser>) {
    setUsers(prev => prev.map(u => u.id === id ? { ...u, ...patch } : u))
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
          <button
            className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${onboardedOnly ? 'bg-brand/20 text-brand border-brand/30' : 'bg-white/[0.04] text-white/30 border-white/10 hover:text-white/60'}`}
            onClick={() => setOnboardedOnly(v => !v)}
            title="Show only onboarded users"
          >
            onboarded only
          </button>
          <span className="text-xs text-white/25">{users.filter(u => !onboardedOnly || u.onboarding_complete).length} / {users.length}</span>
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
              {['', 'Name', 'Wallet', 'USDC', 'ETH', 'Trading Bal', 'Override fee', 'Free fee', 'XP fee', 'Trading Vol', 'Bot PnL', 'Session', ''].map((h, i) => (
                <th key={i} className="text-left px-3 py-2 text-[10px] text-white/25 font-semibold uppercase tracking-wider">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.filter(u => !onboardedOnly || u.onboarding_complete).map(u => (
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

                {/* Free fee period */}
                <td className="px-3 py-2.5">
                  <GrantFreeFeeCell user={u} onUpdated={handleUserPatched} />
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
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {u.active_session_id && (<>
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
                    </>)}
                    <button
                      className="text-[10px] px-2 py-0.5 rounded bg-red-900/30 text-red-400/50 hover:text-red-300 hover:bg-red-900/50 border border-red-500/20 transition-colors"
                      onClick={() => setNuclearUser(u)}
                      title="Cancel orders and/or taker-sell positions directly on Polymarket"
                    >
                      ☢ nuclear
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {users.length === 0 && (
          <div className="text-center py-8 text-white/20 text-xs">No users found.</div>
        )}
      </div>

      {nuclearUser && (
        <NuclearModal user={nuclearUser} onClose={() => setNuclearUser(null)} />
      )}
    </div>
  )
}
