/**
 * Admin Users — list all users with stats, inline fee override edit, session stop controls.
 */
import { useEffect, useRef, useState } from 'react'
import type { AdminUser } from '../../types'
import { api } from '../../utils/api'
import { fmtUsd, pnlColor } from '../../utils/format'
import { useAppStore } from '../../store'
import { AddressField } from '../../components/ui/Field'

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
  outcome: string
  balance: number
  mid: number
  value_usd: number
  resolved: boolean
  condition_id?: string
  collateral_type?: string
}

function NuclearModal({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const [loading, setLoading]               = useState(true)
  const [executing, setExecuting]           = useState(false)
  const [orders, setOrders]                 = useState<PolyOrder[]>([])
  const [positions, setPositions]           = useState<PolyPosition[]>([])
  const [totalValueUsd, setTotalValueUsd]   = useState(0)
  const [selectedOrders, setSelectedOrders] = useState<Set<string>>(new Set())
  const [selectedTokens, setSelectedTokens] = useState<Set<string>>(new Set())
  const [result, setResult]                 = useState<any>(null)
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

  const sellPositions   = positions.filter(p => !p.resolved)
  const redeemPositions = positions.filter(p => p.resolved)

  const sellTotal   = sellPositions.reduce((s, p) => s + (selectedTokens.has(p.token_id) ? p.value_usd : 0), 0)
  const redeemTotal = redeemPositions.reduce((s, p) => s + p.value_usd, 0)

  function toggleToken(tid: string) {
    setSelectedTokens(prev => { const n = new Set(prev); n.has(tid) ? n.delete(tid) : n.add(tid); return n })
  }
  function toggleOrder(oid: string) {
    setSelectedOrders(prev => { const n = new Set(prev); n.has(oid) ? n.delete(oid) : n.add(oid); return n })
  }
  function toggleAllOrders() {
    setSelectedOrders(prev => prev.size === orders.length ? new Set() : new Set(orders.map(o => o.order_id)))
  }

  async function execute() {
    setExecuting(true)
    try {
      const res: any = await api.admin.nuclearClose(user.id, {
        cancel_orders: false,
        cancel_order_ids: Array.from(selectedOrders),
        sell_token_ids: Array.from(selectedTokens),
      })
      setResult(res)
      const sold    = res.sold_positions?.length ?? 0
      const redeemed = res.redeemed_positions?.length ?? 0
      addToast({ type: 'success', message: `Done: ${res.cancelled_orders?.length ?? 0} cancelled · ${sold} sold · ${redeemed} redeemed` })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally {
      setExecuting(false)
    }
  }

  const fmtUsd2 = (v: number) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70" onClick={onClose}>
      <div
        className="bg-[#0d0e10] border border-white/[0.08] rounded-2xl w-full max-w-lg mx-4 p-5 space-y-4 max-h-[85vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-red-400">Nuclear Close</p>
            <p className="text-[10px] text-white/30">{user.display_name || user.id}</p>
          </div>
          <button className="text-white/30 hover:text-white/70 text-sm" onClick={onClose}>✕</button>
        </div>

        {loading ? (
          <p className="text-[11px] text-white/30 text-center py-8">Fetching Poly state…</p>

        ) : result ? (
          /* ── Result view ── */
          <div className="space-y-3">
            <p className="text-[11px] text-emerald-400 font-semibold">Executed</p>

            {/* Summary row */}
            <div className="grid grid-cols-3 gap-2">
              {[
                { label: 'Cancelled', val: result.cancelled_orders?.length ?? 0, color: 'text-white/50' },
                { label: 'Sold', val: result.sold_positions?.length ?? 0, sub: result.sold_positions?.length > 0 ? fmtUsd2(result.sold_positions.reduce((s: number, p: any) => s + (p.estimated_usdc ?? 0), 0)) : null, color: 'text-orange-400' },
                { label: 'Redeemed', val: result.redeemed_positions?.length ?? 0, sub: result.redeemed_positions?.length > 0 ? fmtUsd2(result.redeemed_positions.reduce((s: number, p: any) => s + (p.estimated_usdc ?? 0), 0)) : null, color: 'text-emerald-400' },
              ].map(({ label, val, sub, color }) => (
                <div key={label} className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/[0.06] text-center">
                  <p className={`text-base font-bold ${color}`}>{val}</p>
                  <p className="text-[10px] text-white/30">{label}</p>
                  {sub && <p className="text-[10px] text-white/50 mt-0.5">{sub}</p>}
                </div>
              ))}
            </div>

            {/* Sold detail */}
            {result.sold_positions?.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] text-white/30 font-semibold uppercase tracking-wider">Sold</p>
                {result.sold_positions.map((p: any, i: number) => (
                  <div key={i} className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-white/[0.02] border border-white/[0.05]">
                    <p className="text-[10px] text-white/50 truncate flex-1 pr-2">{p.token_id?.slice(0, 16)}…</p>
                    <div className="text-right shrink-0">
                      <p className="text-[10px] text-white/70">{fmtUsd2(p.estimated_usdc ?? 0)}</p>
                      <p className="text-[9px] text-white/30">{p.tokens?.toFixed(1)} tok @ {((p.price ?? 0) * 100).toFixed(1)}¢</p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Redeemed detail */}
            {result.redeemed_positions?.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] text-emerald-400/60 font-semibold uppercase tracking-wider">Redeemed</p>
                {result.redeemed_positions.map((p: any, i: number) => (
                  <div key={i} className={`flex items-center justify-between px-3 py-1.5 rounded-lg border ${p.ok ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-yellow-500/5 border-yellow-500/20'}`}>
                    <div className="flex-1 min-w-0 pr-2">
                      <p className="text-[10px] text-white/50 truncate">{p.token_id?.slice(0, 16)}…</p>
                      {p.ok && p.tx_hash && (
                        <a
                          href={`https://polygonscan.com/tx/${p.tx_hash}`}
                          target="_blank" rel="noreferrer"
                          className="text-[9px] text-emerald-400/50 hover:text-emerald-400 font-mono"
                        >{p.tx_hash.slice(0, 14)}…</a>
                      )}
                      {!p.ok && p.error && (
                        <p className="text-[9px] text-yellow-400/70">
                          {p.error.includes('collateral not found') ? 'collateral non trouvé' :
                           p.error.includes('STATE_FAILED') || p.error.includes('timeout') ? 'tx rejetée on-chain' :
                           p.error.includes('429') || p.error.includes('Too Many') ? 'rate limit relayer' :
                           p.error.slice(0, 60)}
                        </p>
                      )}
                    </div>
                    <div className="text-right shrink-0">
                      <p className={`text-[10px] font-semibold ${p.ok ? 'text-emerald-400' : 'text-yellow-400'}`}>
                        {p.ok ? '✓ ' : '⚠ '}{fmtUsd2(p.estimated_usdc ?? 0)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Errors */}
            {result.errors?.filter((e: string) => !e.includes('too low to sell')).length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] text-red-400 font-semibold uppercase tracking-wider">
                  Erreurs ({result.errors.filter((e: string) => !e.includes('too low to sell')).length})
                </p>
                <div className="max-h-28 overflow-y-auto space-y-0.5">
                  {result.errors.filter((e: string) => !e.includes('too low to sell')).map((e: string, i: number) => (
                    <p key={i} className="text-[10px] text-red-400/60 font-mono">
                      {e.replace('no bids (market closed, not yet redeemable)', 'marché fermé, pas encore redeemable')
                        .replace('no bids, no condition_id (cannot redeem)', 'pas de condition_id')
                        .replace('all collaterals failed (PRECHECK_SKIPPED or timeout)', 'tous collaterals échoués')
                        .slice(0, 80)}
                    </p>
                  ))}
                </div>
              </div>
            )}

            <button className="text-[11px] text-white/30 hover:text-white/60 pt-1" onClick={onClose}>Close</button>
          </div>

        ) : (
          /* ── Confirmation view ── */
          <>
            {/* Orders */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <p className="text-[10px] text-white/30 font-semibold uppercase tracking-wider">Orders to cancel</p>
                {orders.length > 0 && (
                  <button className="text-[10px] text-white/30 hover:text-white/60" onClick={toggleAllOrders}>
                    {selectedOrders.size === orders.length ? 'Deselect all' : 'Select all'}
                  </button>
                )}
              </div>
              {orders.length === 0 ? (
                <p className="text-[10px] text-white/20 px-1">No open orders</p>
              ) : orders.map(ord => (
                <div key={ord.order_id} className="flex items-center gap-2 px-3 py-2 rounded-xl border border-white/[0.07] bg-white/[0.02] cursor-pointer" onClick={() => toggleOrder(ord.order_id)}>
                  <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${selectedOrders.has(ord.order_id) ? 'bg-red-500/80 border-red-500' : 'border-white/20'}`}>
                    {selectedOrders.has(ord.order_id) && <span className="text-[9px] text-white font-bold">✓</span>}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[11px] text-white/70 truncate">{ord.question || ord.token_id.slice(0, 20) + '…'}</p>
                    <p className="text-[10px] text-white/30 font-mono">{ord.side} · {(ord.price * 100).toFixed(1)}¢ · {ord.size_remaining.toFixed(2)} tok</p>
                  </div>
                </div>
              ))}
            </div>

            {/* Sell positions */}
            {(sellPositions.length > 0 || redeemPositions.length === 0) && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <p className="text-[10px] text-orange-400/70 font-semibold uppercase tracking-wider">Sell at best bid</p>
                  {sellPositions.length > 0 && sellTotal > 0 && (
                    <p className="text-[10px] text-white/40">{fmtUsd2(sellTotal)}</p>
                  )}
                </div>
                {sellPositions.length === 0 ? (
                  <p className="text-[10px] text-white/20 px-1">No sellable positions</p>
                ) : sellPositions.map(pos => (
                  <div key={pos.token_id} className="flex items-center gap-2 px-3 py-2 rounded-xl border border-white/[0.07] bg-white/[0.02] cursor-pointer" onClick={() => toggleToken(pos.token_id)}>
                    <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${selectedTokens.has(pos.token_id) ? 'bg-red-500/80 border-red-500' : 'border-white/20'}`}>
                      {selectedTokens.has(pos.token_id) && <span className="text-[9px] text-white font-bold">✓</span>}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[11px] text-white/70 truncate">{pos.question || pos.token_id.slice(0, 20) + '…'}</p>
                      <p className="text-[10px] text-white/30">
                        {pos.balance.toFixed(2)} tok · <span className="text-white/50">{fmtUsd2(pos.value_usd)}</span> @ {(pos.mid * 100).toFixed(1)}¢
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Redeem positions */}
            {redeemPositions.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <p className="text-[10px] text-emerald-400/80 font-semibold uppercase tracking-wider">Redeem on-chain</p>
                    <span className="text-[9px] bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 rounded-full">resolved</span>
                  </div>
                  <p className="text-[10px] text-emerald-400/70">{fmtUsd2(redeemTotal)}</p>
                </div>
                {redeemPositions.map(pos => (
                  <div key={pos.token_id} className="flex items-center gap-2 px-3 py-2 rounded-xl border border-emerald-500/20 bg-emerald-500/5">
                    <div className="w-5 h-5 rounded bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center shrink-0">
                      <span className="text-[9px] text-emerald-400 font-bold">$</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-[11px] text-white/70 truncate flex-1">{pos.question || pos.token_id.slice(0, 20) + '…'}</p>
                        {pos.outcome && <span className="text-[9px] bg-white/10 text-white/50 px-1 py-0.5 rounded shrink-0">{pos.outcome}</span>}
                      </div>
                      <p className="text-[10px] text-emerald-400/60">
                        {pos.balance.toFixed(2)} tok · <span className="text-emerald-400/80 font-semibold">{fmtUsd2(pos.value_usd)}</span>
                        {pos.condition_id && <span className="text-white/20 ml-1 font-mono">{pos.condition_id.slice(0, 10)}…</span>}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Warning */}
            <div className="px-3 py-2.5 rounded-xl bg-red-500/5 border border-red-500/20">
              <p className="text-[10px] text-red-400/70">
                Sell orders execute at best bid (taker). Redeems submit on-chain via Safe. Cannot be undone.
              </p>
            </div>

            <button
              className="w-full py-2.5 rounded-xl bg-red-500/20 border border-red-500/30 text-red-400 text-[11px] font-semibold hover:bg-red-500/30 transition-colors disabled:opacity-40"
              onClick={execute}
              disabled={executing || (selectedOrders.size === 0 && selectedTokens.size === 0 && redeemPositions.length === 0)}
            >
              {executing ? 'Executing…' : `Execute — ${fmtUsd2(sellTotal + redeemTotal)}`}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

// ── Redeem Only Modal ────────────────────────────────────────────────────────

function RedeemModal({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const [loading, setLoading]     = useState(true)
  const [executing, setExecuting] = useState(false)
  const [positions, setPositions] = useState<PolyPosition[]>([])
  const [result, setResult]       = useState<any>(null)
  const addToast = useAppStore(s => s.addToast)

  useEffect(() => {
    ;(async () => {
      try {
        const d: any = await api.admin.getUserPolyState(user.id)
        const resolved = (d.positions || []).filter((p: PolyPosition) => p.resolved)
        setPositions(resolved)
      } catch (e: any) {
        addToast({ type: 'error', message: e.message })
        onClose()
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const totalUsd = positions.reduce((s, p) => s + p.balance, 0)

  async function execute() {
    setExecuting(true)
    try {
      const res: any = await api.admin.nuclearClose(user.id, {
        cancel_orders: false,
        cancel_order_ids: [],
        sell_token_ids: positions.map(p => p.token_id),
      })
      setResult(res)
      const n = res.redeemed_positions?.length ?? 0
      addToast({ type: 'success', message: `Redeemed: ${n} position${n !== 1 ? 's' : ''}` })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally {
      setExecuting(false)
    }
  }

  const fmtDollar = (v: number) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70" onClick={onClose}>
      <div
        className="bg-[#0d0e10] border border-white/[0.08] rounded-2xl w-full max-w-md mx-4 p-5 space-y-4 max-h-[85vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-emerald-400">Redeem positions</p>
            <p className="text-[10px] text-white/30">{user.display_name || user.id}</p>
          </div>
          <button className="text-white/30 hover:text-white/70 text-sm" onClick={onClose}>✕</button>
        </div>

        {loading ? (
          <p className="text-[11px] text-white/30 text-center py-8">Fetching positions…</p>

        ) : result ? (
          <div className="space-y-3">
            <p className="text-[11px] text-emerald-400 font-semibold">Done</p>
            {result.redeemed_positions?.map((p: any, i: number) => (
              <div key={i} className={`flex items-center justify-between px-3 py-2 rounded-xl border ${p.ok ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-yellow-500/5 border-yellow-500/20'}`}>
                <div className="flex-1 min-w-0 pr-2">
                  <p className="text-[10px] text-white/60 truncate">{p.question || p.token_id?.slice(0, 20) + '…'}</p>
                  {p.ok && p.tx_hash && (
                    <a href={`https://polygonscan.com/tx/${p.tx_hash}`} target="_blank" rel="noreferrer"
                      className="text-[9px] text-emerald-400/50 hover:text-emerald-400 font-mono"
                    >{p.tx_hash.slice(0, 14)}…</a>
                  )}
                  {!p.ok && p.error && <p className="text-[9px] text-yellow-400/70">{p.error.slice(0, 60)}</p>}
                </div>
                <p className={`text-[10px] font-semibold shrink-0 ${p.ok ? 'text-emerald-400' : 'text-yellow-400'}`}>
                  {p.ok ? '✓ ' : '⚠ '}{fmtDollar(p.estimated_usdc ?? 0)}
                </p>
              </div>
            ))}
            {result.errors?.length > 0 && (
              <div className="space-y-0.5">
                {result.errors.map((e: string, i: number) => (
                  <p key={i} className="text-[10px] text-red-400/60 font-mono">{e.slice(0, 80)}</p>
                ))}
              </div>
            )}
            <button className="text-[11px] text-white/30 hover:text-white/60 pt-1" onClick={onClose}>Close</button>
          </div>

        ) : positions.length === 0 ? (
          <div className="text-center py-8 space-y-1">
            <p className="text-[12px] text-white/40">No resolved positions</p>
            <p className="text-[10px] text-white/20">Only resolved (winning) positions can be redeemed</p>
            <button className="mt-3 text-[11px] text-white/30 hover:text-white/60" onClick={onClose}>Close</button>
          </div>

        ) : (
          <>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-[10px] text-white/30 font-semibold uppercase tracking-wider">Resolved positions</p>
                <p className="text-[10px] text-emerald-400/80 font-semibold">{fmtDollar(totalUsd)} total</p>
              </div>
              {positions.map(pos => {
                const collateral = pos.collateral_type || (pos.condition_id ? 'pUSD' : 'USDC.e')
                return (
                  <div key={pos.token_id} className="flex items-center gap-2 px-3 py-2.5 rounded-xl border border-emerald-500/20 bg-emerald-500/5">
                    <div className="w-5 h-5 rounded bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center shrink-0">
                      <span className="text-[9px] text-emerald-400 font-bold">$</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-[11px] text-white/80 truncate flex-1">{pos.question || pos.token_id.slice(0, 20) + '…'}</p>
                        {pos.outcome && <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded shrink-0">{pos.outcome}</span>}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <p className="text-[10px] text-emerald-400 font-semibold">{fmtDollar(pos.balance)}</p>
                        <span className="text-[9px] text-white/30">{pos.balance.toFixed(2)} tokens × $1.00</span>
                        <span className="text-[9px] text-white/25 bg-white/[0.05] px-1.5 py-0.5 rounded font-mono">{collateral}</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="px-3 py-2.5 rounded-xl bg-emerald-500/5 border border-emerald-500/20">
              <p className="text-[10px] text-emerald-400/70">
                Winning positions — each token redeems at $1.00 on-chain via Safe.
              </p>
            </div>

            <button
              className="w-full py-2.5 rounded-xl bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 text-[11px] font-semibold hover:bg-emerald-500/30 transition-colors disabled:opacity-40"
              onClick={execute}
              disabled={executing}
            >
              {executing ? 'Redeeming…' : `Redeem ${positions.length} position${positions.length !== 1 ? 's' : ''} — ${fmtDollar(totalUsd)}`}
            </button>
          </>
        )}
      </div>
    </div>
  )
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

function BuilderCodeCell({ user, onUpdated }: {
  user: AdminUser
  onUpdated: (id: string, patch: Partial<AdminUser>) => void
}) {
  const [showMenu, setShowMenu] = useState(false)
  const [daysInput, setDaysInput] = useState('7')
  const [saving, setSaving] = useState(false)
  const addToast = useAppStore(s => s.addToast)

  const freeDaysLeft = (() => {
    if (!user.free_fee_ends_at) return 0
    const ms = new Date(user.free_fee_ends_at).getTime() - Date.now()
    return ms > 0 ? Math.ceil(ms / 86_400_000) : 0
  })()

  const isOff = user.fee_override_bps === 0
  const isTimed = isOff && freeDaysLeft > 0

  const turnOn = async () => {
    setSaving(true)
    try {
      await api.admin.updateUser(user.id, { fee_override_bps: -1 })
      onUpdated(user.id, { fee_override_bps: null, free_fee_ends_at: null, builder_code_enabled: true })
      addToast({ type: 'success', message: 'Builder code ON' })
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setSaving(false); setShowMenu(false) }
  }

  const turnOffPermanent = async () => {
    setSaving(true)
    try {
      await api.admin.updateUser(user.id, { fee_override_bps: 0 })
      onUpdated(user.id, { fee_override_bps: 0, free_fee_ends_at: null, builder_code_enabled: false })
      addToast({ type: 'success', message: 'Builder code OFF (permanent)' })
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setSaving(false); setShowMenu(false) }
  }

  const turnOffTimed = async () => {
    const n = parseInt(daysInput)
    if (!n || n < 1) return
    setSaving(true)
    try {
      await api.admin.updateUser(user.id, { free_fee_days: n })
      const ends = new Date(Date.now() + n * 86_400_000).toISOString()
      onUpdated(user.id, { fee_override_bps: 0, free_fee_ends_at: ends, builder_code_enabled: false })
      addToast({ type: 'success', message: `Builder code OFF for ${n}d` })
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setSaving(false); setShowMenu(false) }
  }

  const badge = isTimed
    ? <span className="text-[10px] font-mono text-orange-400">OFF·{freeDaysLeft}d</span>
    : isOff
    ? <span className="text-[10px] font-mono text-red-400">OFF</span>
    : <span className="text-[10px] font-mono text-emerald-400">ON</span>

  return (
    <div className="relative">
      <button
        onClick={() => setShowMenu(m => !m)}
        disabled={saving}
        className="flex items-center gap-1 cursor-pointer"
      >
        {badge}
        <span className="text-white/20 text-[9px]">▾</span>
      </button>
      {showMenu && (
        <div className="absolute z-50 top-full left-0 mt-1 bg-[#1a1a2e] border border-white/10 rounded-lg shadow-xl p-2 flex flex-col gap-1 min-w-[140px]">
          {isOff ? (
            <button onClick={turnOn} disabled={saving} className="text-[10px] text-emerald-400 hover:text-emerald-300 text-left px-2 py-1 rounded hover:bg-white/5">
              → Turn ON
            </button>
          ) : (
            <button onClick={turnOffPermanent} disabled={saving} className="text-[10px] text-red-400 hover:text-red-300 text-left px-2 py-1 rounded hover:bg-white/5">
              → Turn OFF (permanent)
            </button>
          )}
          <div className="flex items-center gap-1 px-2 py-1">
            <span className="text-[9px] text-white/30">OFF for</span>
            <input
              type="number" min={1} max={365}
              value={daysInput} onChange={e => setDaysInput(e.target.value)}
              className="w-10 text-[10px] font-mono bg-white/5 border border-white/10 rounded px-1 py-0.5 text-white/80"
            />
            <span className="text-[9px] text-white/30">d</span>
            <button onClick={turnOffTimed} disabled={saving} className="text-[9px] text-orange-400 hover:text-orange-300">✓</button>
          </div>
          <button onClick={() => setShowMenu(false)} className="text-[9px] text-white/20 hover:text-white/40 text-left px-2 py-0.5">cancel</button>
        </div>
      )}
    </div>
  )
}

// ── User Session History Overlay ─────────────────────────────────────────────

interface AdminHistorySession {
  session_id:       string
  status:           string
  gross_capital:    number
  stop_mode:        string
  stop_days:        number | null
  target_volume:    number | null
  started_at:       string | null
  stopped_at:       string | null
  stop_reason:      string | null
  total_volume:     number
  realized_pnl:     number
  unrealized_pnl:   number
  builder_fees_usdc: number
  paid_fees:        number
  trade_count:      number
  order_count:      number
}

function fmtShortDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function fmtDurShort(s: number): string {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  if (d > 0) return `${d}d ${h}h`
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function SessionDetailPanel({ s, onBack }: { s: AdminHistorySession; onBack: () => void }) {
  const newFeeModel = s.stopped_at != null ? s.stopped_at >= '2026-05-13' : true
  const fees = newFeeModel ? s.builder_fees_usdc : s.paid_fees
  const netPnl = s.realized_pnl - fees
  const cpm = s.total_volume > 0 ? (netPnl / s.total_volume) * 1_000_000 : null
  const isRunning = s.status === 'running' || s.status === 'starting'

  const startMs = s.started_at ? new Date(s.started_at).getTime() : null
  const endMs = s.stopped_at ? new Date(s.stopped_at).getTime() : Date.now()
  const uptimeS = startMs ? (endMs - startMs) / 1000 : 0

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06]">
        <button onClick={onBack} className="text-white/30 hover:text-white/60 text-sm">←</button>
        <div className="flex-1 min-w-0">
          <span className={`text-[10px] font-mono font-semibold ${
            s.status === 'running' ? 'text-emerald-400' :
            s.status === 'error' ? 'text-red-400' : 'text-white/40'
          }`}>{s.status}</span>
          <span className="ml-2 text-[10px] text-white/25">{fmtShortDate(s.started_at)}</span>
        </div>
        <span className="text-[11px] font-mono font-semibold text-white/60">{fmtUsd(s.gross_capital, 0)}</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {/* Stats grid */}
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'Volume', value: fmtUsd(s.total_volume, 0), color: 'text-white/60' },
            { label: 'Uptime', value: uptimeS > 0 ? fmtDurShort(uptimeS) : '—', color: 'text-white/40' },
            { label: 'Gross PnL', value: fmtUsd(s.realized_pnl), color: pnlColor(s.realized_pnl) },
            { label: isRunning ? 'Builder Fees' : 'Fees', value: fmtUsd(fees), color: 'text-white/30' },
            { label: 'Net PnL', value: fmtUsd(netPnl), color: pnlColor(netPnl) },
            { label: 'CPM', value: cpm != null ? `${cpm > 0 ? '+' : ''}${fmtUsd(cpm, 0)}` : '—', color: cpm != null ? pnlColor(cpm) : 'text-white/20' },
            { label: 'Trades', value: String(s.trade_count), color: 'text-white/40' },
            { label: 'Orders', value: String(s.order_count), color: 'text-white/25' },
          ].map(({ label, value, color }) => (
            <div key={label} className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/[0.05]">
              <div className="text-[9px] text-white/25 uppercase tracking-widest mb-0.5">{label}</div>
              <div className={`text-[13px] font-mono font-semibold tabular-nums ${color}`}>{value}</div>
            </div>
          ))}
        </div>

        {/* Progress */}
        {s.stop_mode === 'days' && s.stop_days && (
          <div className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/[0.05]">
            <div className="text-[9px] text-white/25 uppercase tracking-widest mb-1">Duration</div>
            <div className="text-[11px] font-mono text-white/40">
              {fmtDurShort(uptimeS)} / {s.stop_days}d
              <span className="ml-2 text-white/20">
                ({Math.min(100, (uptimeS / (s.stop_days * 86400)) * 100).toFixed(0)}%)
              </span>
            </div>
          </div>
        )}
        {s.stop_mode === 'volume' && s.target_volume && (
          <div className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/[0.05]">
            <div className="text-[9px] text-white/25 uppercase tracking-widest mb-1">Volume target</div>
            <div className="text-[11px] font-mono text-white/40">
              {fmtUsd(s.total_volume, 0)} / {fmtUsd(s.target_volume, 0)}
              <span className="ml-2 text-white/20">
                ({Math.min(100, (s.total_volume / s.target_volume) * 100).toFixed(0)}%)
              </span>
            </div>
          </div>
        )}

        {/* Stop reason */}
        {s.stop_reason && (
          <div className="px-3 py-2 rounded-xl bg-red-500/5 border border-red-500/15">
            <div className="text-[9px] text-red-400/60 uppercase tracking-widest mb-0.5">Stop reason</div>
            <div className="text-[10px] font-mono text-red-400/70">{s.stop_reason}</div>
          </div>
        )}
      </div>
    </div>
  )
}

function UserHistoryOverlay({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const [sessions, setSessions]     = useState<AdminHistorySession[]>([])
  const [loading, setLoading]       = useState(true)
  const [selected, setSelected]     = useState<AdminHistorySession | null>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.admin.getUserSessionHistory(user.id)
      .then((d: any) => setSessions(d as AdminHistorySession[]))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [user.id])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') { if (selected) setSelected(null); else onClose() } }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, selected])

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
      onClick={e => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className="bg-[#0d0e10] border border-white/[0.08] rounded-2xl w-full max-w-md shadow-2xl flex flex-col"
        style={{ maxHeight: '80vh' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] shrink-0">
          <div>
            <span className="text-sm font-semibold text-white/80">{user.display_name || user.id.slice(0, 12)}</span>
            <span className="ml-2 text-[10px] text-white/25 font-mono">{user.wallet_address?.slice(0, 8)}…</span>
          </div>
          <button className="w-6 h-6 flex items-center justify-center text-white/20 hover:text-white/55" onClick={onClose}>✕</button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-hidden">
          {selected ? (
            <SessionDetailPanel s={selected} onBack={() => setSelected(null)} />
          ) : loading ? (
            <div className="p-6 space-y-px">
              {[1,2,3].map(i => <div key={i} className="h-10 bg-white/[0.025] animate-pulse rounded-lg" />)}
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2">
              <div className="text-white/20 text-xs font-mono uppercase tracking-widest">No activity</div>
              <div className="text-white/10 text-[10px]">No sessions found for this user</div>
            </div>
          ) : (
            <div className="overflow-y-auto" style={{ maxHeight: 'calc(80vh - 56px)' }}>
              {sessions.map(s => {
                const newFeeModel = s.stopped_at != null ? s.stopped_at >= '2026-05-13' : true
                const fees = newFeeModel ? s.builder_fees_usdc : s.paid_fees
                const netPnl = s.realized_pnl - fees
                const isRunning = s.status === 'running' || s.status === 'starting'
                return (
                  <div
                    key={s.session_id}
                    className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.04] last:border-0 hover:bg-white/[0.025] cursor-pointer transition-colors"
                    onClick={() => setSelected(s)}
                  >
                    <div className="flex flex-col items-center gap-0.5 shrink-0">
                      <span className={`w-2 h-2 rounded-full ${
                        isRunning ? 'bg-emerald-400 animate-pulse' :
                        s.status === 'error' ? 'bg-red-400' : 'bg-white/15'
                      }`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-mono text-white/35">{fmtShortDate(s.stopped_at || s.started_at)}</span>
                        <span className="text-[9px] font-mono text-white/20">{fmtUsd(s.gross_capital, 0)}</span>
                      </div>
                      <div className="text-[10px] font-mono text-white/25 mt-0.5">
                        vol {fmtUsd(s.total_volume, 0)} · {s.trade_count} trades
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(netPnl)}`}>{fmtUsd(netPnl)}</div>
                      <div className="text-[9px] font-mono text-white/20">{fmtUsd(fees)} fees</div>
                    </div>
                    <span className="text-white/15 text-xs shrink-0">›</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ActionsMenu({ user, onClose, onStop, onNuclear, onRedeem }: {
  user: AdminUser
  onClose: () => void
  onStop: (mode: 'soft' | 'hard') => void
  onNuclear: () => void
  onRedeem: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const addToast = useAppStore(s => s.addToast)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    try { await fn() } finally { setBusy(null); onClose() }
  }

  const bridgePol = () => run('pol', async () => {
    const r: any = await api.admin.bridgePol(user.id)
    if (r?.ok) addToast({ type: 'success', message: `POL bridge sent${r.tx_hash ? ' · ' + r.tx_hash.slice(0, 10) + '…' : ''}` })
    else addToast({ type: 'error', message: r?.error || 'Bridge failed' })
  })

  const fixClob2 = () => run('clob2', async () => {
    const r: any = await api.admin.fixClob2(user.id)
    if (r?.ok) addToast({ type: 'success', message: `CLOB V2 fixed: ${r.steps?.join(', ')}` })
    else addToast({ type: 'error', message: r?.detail || 'Fix CLOB V2 failed' })
  })

  const migrateV2 = () => run('v2', async () => {
    const r: any = await api.admin.migrateV2(user.id)
    if (r?.ok) addToast({ type: 'success', message: `V2 migration done${r.tx_hash ? ' · ' + r.tx_hash.slice(0, 10) + '…' : ''}` })
    else addToast({ type: 'error', message: r?.detail || 'Migration failed' })
  })

  const sep = <div className="my-1 border-t border-white/[0.06]" />

  const item = (label: string, sub: string, color: string, onClick: () => void, disabled = false) => (
    <button
      disabled={disabled || busy !== null}
      className={`flex w-full flex-col px-4 py-2.5 text-left hover:bg-white/[0.05] transition-colors disabled:opacity-30 ${color}`}
      onClick={onClick}
    >
      <span className="text-[12px] font-medium leading-tight">{label}</span>
      <span className="text-[10px] text-white/30 leading-tight mt-0.5">{sub}</span>
    </button>
  )

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm px-4"
      onClick={e => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className="bg-[#0f1012] border border-white/[0.08] rounded-2xl w-full max-w-xs shadow-2xl overflow-hidden">
        <div className="flex items-center justify-between px-4 pt-4 pb-3 border-b border-white/[0.05]">
          <div>
            <span className="text-sm font-semibold text-white/80">{user.display_name || user.id.slice(0, 12)}</span>
            <span className="ml-2 text-[10px] font-mono text-white/25">{user.wallet_address?.slice(0, 8)}…</span>
          </div>
          <button className="w-6 h-6 flex items-center justify-center rounded-lg text-white/20 hover:text-white/55 hover:bg-white/[0.06] transition-all" onClick={onClose}>✕</button>
        </div>

        <div className="py-1.5">
          {user.active_session_id && <>
            {item('Soft stop', 'Graceful shutdown, waits for open orders', 'text-white/70', () => { onClose(); onStop('soft') })}
            {item('Hard stop', 'Immediate stop, cancels all orders now', 'text-red-400/80', () => { onClose(); onStop('hard') })}
            {sep}
          </>}
          {item('Redeem positions', 'Redeem resolved (winning) positions on-chain', 'text-emerald-400/70', () => { onClose(); onRedeem() })}
          {item('Nuclear close', 'Cancel orders + sell / redeem all positions', 'text-red-400/70', () => { onClose(); onNuclear() })}
          {sep}
          {item('Bridge POL', 'Send $0.50 USDC (Arb) → POL on Polygon for gas', 'text-yellow-400/70', bridgePol)}
          {item('Fix CLOB V2', "Recreate API key + index Safe pUSD with CLOB V2", 'text-cyan-400/80', fixClob2)}
          {item('Migrate V2', 'Re-run pUSD + CTF × 3 approval txs on Safe', 'text-purple-400/60', migrateV2)}
        </div>

        {busy && (
          <div className="px-4 py-2 border-t border-white/[0.05] text-[10px] text-white/30 animate-pulse">
            Running {busy}…
          </div>
        )}
      </div>
    </div>
  )
}

function ActionsDropdown({ user, onStop, stopping, onNuclear, onRedeem }: {
  user: AdminUser
  onStop: (mode: 'soft' | 'hard') => void
  stopping: boolean
  onNuclear: () => void
  onRedeem: () => void
}) {
  const [open, setOpen] = useState(false)
  const isBusy = stopping

  return (
    <>
      <button
        className={`text-[11px] px-2 py-0.5 rounded bg-white/[0.05] text-white/40 hover:text-white/80 hover:bg-white/10 border border-white/10 transition-colors disabled:opacity-30 ${isBusy ? 'animate-pulse' : ''}`}
        onClick={() => setOpen(true)}
        disabled={isBusy}
        title="Actions"
      >
        {isBusy ? '…' : '···'}
      </button>
      {open && (
        <ActionsMenu
          user={user}
          onClose={() => setOpen(false)}
          onStop={onStop}
          onNuclear={onNuclear}
          onRedeem={onRedeem}
        />
      )}
    </>
  )
}

const BALANCE_POLL_MS = 30_000

export default function AdminUsers() {
  const [users, setUsers]         = useState<AdminUser[]>([])
  const [loading, setLoading]     = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [stopping, setStopping]       = useState<string | null>(null)
  const [nuclearUser, setNuclearUser]   = useState<AdminUser | null>(null)
  const [redeemUser, setRedeemUser]     = useState<AdminUser | null>(null)
  const [historyUser, setHistoryUser]   = useState<AdminUser | null>(null)
  const [filterAccountReady, setFilterAccountReady] = useState(false)
  const [filterFunded, setFilterFunded] = useState(true)
  const [forcingReplaceAll, setForcingReplaceAll] = useState(false)
  const addToast = useAppStore(s => s.addToast)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function handleForceReplaceAll() {
    setForcingReplaceAll(true)
    try {
      const res: any = await api.admin.forceReplaceAll()
      addToast({ type: 'success', message: `Force replace all: ${res.triggered} slots triggered${res.errors?.length ? `, ${res.errors.length} errors` : ''}` })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message ?? 'Force replace all failed' })
    } finally {
      setForcingReplaceAll(false)
    }
  }

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
            className="text-[10px] px-2 py-0.5 rounded border border-orange-500/30 bg-orange-500/10 text-orange-400 hover:bg-orange-500/20 transition-colors disabled:opacity-40"
            onClick={handleForceReplaceAll}
            disabled={forcingReplaceAll}
            title="Force replace all queued replacements on all running sessions"
          >
            {forcingReplaceAll ? 'replacing…' : '⟳ force replace all'}
          </button>
          <button
            className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${filterAccountReady ? 'bg-brand/20 text-brand border-brand/30' : 'bg-white/[0.04] text-white/30 border-white/10 hover:text-white/60'}`}
            onClick={() => setFilterAccountReady(v => !v)}
            title="Show only account-ready users"
          >
            Account ready
          </button>
          <button
            className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${filterFunded ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 'bg-white/[0.04] text-white/30 border-white/10 hover:text-white/60'}`}
            onClick={() => setFilterFunded(v => !v)}
            title="Show only funded users"
          >
            Funded
          </button>
          <span className="text-xs text-white/25">
            {users.filter(u =>
              (!filterAccountReady || u.account_ready) &&
              (!filterFunded || u.is_funded)
            ).length} / {users.length}
          </span>
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
              {['', 'Name', 'Wallet', 'USDC', 'ETH', 'POL', 'Trading Bal', 'Builder Code', 'Trading Vol', 'Bot PnL', 'Session', '⋯'].map((h, i) => (
                <th key={i} className="text-left px-3 py-2 text-[10px] text-white/25 font-semibold uppercase tracking-wider">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.filter(u =>
              (!filterAccountReady || u.account_ready) &&
              (!filterFunded || u.is_funded)
            ).map(u => (
              <tr
                key={u.id}
                className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors cursor-pointer"
                onClick={() => setHistoryUser(u)}
              >
                {/* Status dot */}
                <td className="px-3 py-2.5">
                  <StatusDot live={u.session_live} status={u.session_status} />
                </td>

                {/* Name */}
                <td className="px-3 py-2.5 max-w-[160px]">
                  <div className="truncate text-white/70">
                    {u.display_name || <span className="text-white/20">—</span>}
                    {u.is_admin && (
                      <span className="ml-1 text-[9px] text-brand/70 font-semibold">ADMIN</span>
                    )}
                  </div>
                  <div className="flex gap-1 mt-0.5">
                    {u.account_ready && (
                      <span className="text-[8px] px-1 py-0 rounded bg-emerald-500/15 text-emerald-400 font-semibold">READY</span>
                    )}
                    {u.is_funded && (
                      <span className="text-[8px] px-1 py-0 rounded bg-blue-500/15 text-blue-400 font-semibold">FUNDED</span>
                    )}
                  </div>
                </td>

                {/* Wallet */}
                <td className="px-3 py-2.5 max-w-[130px]">
                  {u.wallet_address
                    ? <AddressField value={u.wallet_address} />
                    : <span className="text-white/20 font-mono text-xs">—</span>
                  }
                </td>

                {/* Wallet USDC (Arb EOA — debug only) */}
                <td className="px-3 py-2.5 font-mono text-xs text-white/20">
                  {fmtUsdc(u.wallet_usdc)}
                </td>

                {/* Wallet ETH (Arb EOA — debug only) */}
                <td className="px-3 py-2.5 font-mono text-xs text-white/20">
                  {fmtEth(u.wallet_eth)}
                </td>

                {/* Wallet POL */}
                <td className={`px-3 py-2.5 font-mono text-xs ${
                  u.wallet_pol == null    ? 'text-white/25'
                  : u.wallet_pol < 0.01  ? 'text-red-400'
                  : u.wallet_pol < 0.05  ? 'text-yellow-400'
                  : 'text-emerald-400'
                }`}>
                  {u.wallet_pol == null ? '—' : u.wallet_pol < 0.001 ? '<0.001' : u.wallet_pol.toFixed(3)}
                </td>

                {/* Trading balance */}
                <td className={`px-3 py-2.5 font-mono text-xs ${
                  u.trading_balance == null ? 'text-white/25'
                  : u.trading_balance < 20  ? 'text-red-400'
                  : u.trading_balance < 80  ? 'text-yellow-400'
                  : 'text-emerald-400'
                }`}>
                  {fmtUsdc(u.trading_balance)}
                  {u.trading_balance != null && (u.positions_value ?? 0) + (u.redeem_value ?? 0) > 0 && (() => {
                    const avail = u.trading_balance - (u.positions_value ?? 0) - (u.redeem_value ?? 0)
                    return (
                      <div className="text-[9px] font-normal text-white/30 leading-tight mt-0.5">
                        <div>avail {fmtUsdc(avail)}</div>
                        {(u.redeem_value ?? 0) >= 5 && <div>redeem {fmtUsdc(u.redeem_value)}</div>}
                      </div>
                    )
                  })()}
                </td>

                {/* Builder code toggle */}
                <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                  <BuilderCodeCell user={u} onUpdated={handleUserPatched} />
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

                {/* Actions */}
                <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                  <ActionsDropdown
                    user={u}
                    onStop={(mode) => handleStop(u.id, mode)}
                    stopping={stopping === u.id + 'soft' || stopping === u.id + 'hard'}
                    onNuclear={() => setNuclearUser(u)}
                    onRedeem={() => setRedeemUser(u)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {users.length === 0 && (
          <div className="text-center py-8 text-white/20 text-xs">No users found.</div>
        )}
      </div>

      {historyUser && (
        <UserHistoryOverlay user={historyUser} onClose={() => setHistoryUser(null)} />
      )}
      {nuclearUser && (
        <NuclearModal user={nuclearUser} onClose={() => setNuclearUser(null)} />
      )}
      {redeemUser && (
        <RedeemModal user={redeemUser} onClose={() => setRedeemUser(null)} />
      )}
    </div>
  )
}
