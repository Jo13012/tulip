import { useEffect, useState, useCallback, useRef } from 'react'
import { api } from '../../utils/api'
import { useAppStore } from '../../store'
import { fmtUsd, pnlColor } from '../../utils/format'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SubMarket {
  question: string; slug: string; url: string
  condition_id: string; token_id_yes: string; token_id_no: string
  tick_size: string; neg_risk: boolean; active: boolean; closed: boolean
}

interface SlotMarket {
  polymarket_url: string; question: string | null; side: string
  price_min: number; price_max: number; number_of_lots: number
  sl_ticks: number; spread_from_ask: boolean
  token_id_yes?: string; token_id_no?: string
}

interface Slot {
  id: string; label: string; allocation_pct: number
  enabled: boolean; order_index: number; market: SlotMarket | null
}

interface Strategy {
  id: string; name: string; status: string
  reserve_pct: number; fee_side: string
  global_pause: boolean; version: string
  slots: Slot[]; versions: any[]
  min_capital_draft: number | null
  min_capital_published: number | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MIN_LOT_USDC = 5

function computeMinCapital(slots: Slot[], reservePct: number): number {
  const enabled = slots.filter(s => s.enabled && s.allocation_pct > 0)
  if (!enabled.length) return 0
  let minDep = 0
  for (const sl of enabled) {
    const levels = (sl.market?.number_of_lots ?? 2)
    const allocFrac = sl.allocation_pct / 100
    if (allocFrac <= 0) continue
    minDep = Math.max(minDep, (levels * MIN_LOT_USDC) / allocFrac)
  }
  const factor = 1 - reservePct / 100
  return factor > 0 ? Math.round(minDep / factor * 100) / 100 : minDep
}

function cls(...args: (string | false | undefined | null)[]) {
  return args.filter(Boolean).join(' ')
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

const MIN_PRICE_CENTS = 1.0
const MAX_PRICE_CENTS = 99.9

function round3(value: number) {
  return Math.round(value * 1000) / 1000
}

function formatPriceCents(price: number) {
  if (!Number.isFinite(price)) return ''
  return (price * 100).toFixed(1)
}

function clampCents(value: number) {
  return Math.min(MAX_PRICE_CENTS, Math.max(MIN_PRICE_CENTS, value))
}

function parseCentsInput(value: string) {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number.parseFloat(trimmed)
  if (!Number.isFinite(parsed)) return null
  return parsed
}

// Extracts last path segment (slug) from a polymarket URL
function extractSlug(raw: string) {
  const clean = raw.trim().split(/[?#]/)[0].replace(/\/$/, '')
  return clean.split('/').filter(Boolean).pop() || raw.trim()
}

// Extracts event slug (first segment after /event/) from a sub-market URL
function extractEventSlug(url: string) {
  const m = url.match(/\/event\/([^/?#]+)/)
  return m ? m[1] : extractSlug(url)
}

// True if URL looks like a sub-market (has 2 meaningful path segments after /event/)
function isSubMarketUrl(url: string) {
  const m = url.match(/\/event\/([^/?#]+)\/([^/?#]+)/)
  return !!(m && m[2] && m[2] !== m[1])
}

// ── Live orderbook widget ─────────────────────────────────────────────────────

function OrderbookWidget({ tokenId, side, onBestBid }: { tokenId: string; side: string; onBestBid?: (bid: number | null) => void }) {
  const [book, setBook] = useState<{ bids: any[]; asks: any[] } | null>(null)
  const [err, setErr]   = useState(false)

  useEffect(() => {
    if (!tokenId) return
    setBook(null); setErr(false)
    let cancelled = false

    async function fetchBook() {
      try {
        const data: any = await api.admin.getBook(tokenId)
        if (!cancelled) {
          setBook(data)
          onBestBid?.(data?.bids?.[0]?.price ?? null)
        }
      } catch { if (!cancelled) { setErr(true); onBestBid?.(null) } }
    }

    fetchBook()
    const iv = setInterval(fetchBook, 3000)
    return () => { cancelled = true; clearInterval(iv) }
  }, [tokenId])

  if (err) return <div className="text-[10px] text-red-400/60 italic">Orderbook unavailable (Rust service)</div>
  if (!book) return <div className="text-[10px] text-white/20 animate-pulse">Loading orderbook…</div>

  const bids = book.bids.slice(0, 5)
  const asks = book.asks.slice(0, 5)
  const bestBid = bids[0]?.price
  const bestAsk = asks[0]?.price
  const mid = bestBid && bestAsk ? ((bestBid + bestAsk) / 2 * 100).toFixed(1) : '—'
  const spread = bestBid && bestAsk ? ((bestAsk - bestBid) * 100).toFixed(1) : '—'
  const fmt = (p: number) => (p * 100).toFixed(1)

  return (
    <div className="rounded border border-white/[0.06] overflow-hidden text-[10px]">
      <div className="flex items-center justify-between px-3 py-1.5 bg-white/[0.02] border-b border-white/[0.04]">
        <span className="text-white/40">Orderbook <span className={side === 'YES' ? 'text-green-400' : 'text-red-400'}>{side}</span></span>
        <span className="text-white/30">mid <span className="text-white/60 font-mono">{mid}¢</span> · spread <span className="text-white/60">{spread}¢</span></span>
      </div>
      <div className="grid grid-cols-2 divide-x divide-white/[0.04]">
        <div className="p-2 space-y-0.5">
          <div className="text-green-400/60 mb-1">Bids</div>
          {bids.map((b, i) => (
            <div key={i} className="flex justify-between font-mono">
              <span className="text-green-400">{fmt(b.price)}¢</span>
              <span className="text-white/30">{b.size.toFixed(1)}</span>
            </div>
          ))}
          {!bids.length && <div className="text-white/20 italic">empty</div>}
        </div>
        <div className="p-2 space-y-0.5">
          <div className="text-red-400/60 mb-1">Asks</div>
          {asks.map((a, i) => (
            <div key={i} className="flex justify-between font-mono">
              <span className="text-red-400">{fmt(a.price)}¢</span>
              <span className="text-white/30">{a.size.toFixed(1)}</span>
            </div>
          ))}
          {!asks.length && <div className="text-white/20 italic">empty</div>}
        </div>
      </div>
    </div>
  )
}

// ── Market search + config — unified panel ───────────────────────────────────

const DEFAULT_CFG = { side: 'YES', price_min: 0.99, price_max: 0.999, number_of_lots: 2, sl_ticks: 0, spread_from_ask: false }

function MarketPanel({
  initial,
  onSave,
  onCancel,
  label,
}: {
  initial: SlotMarket | null
  onSave: (m: SlotMarket) => Promise<void>
  onCancel: () => void
  label: string
}) {
  const addToast = useAppStore(s => s.addToast)

  // URL input state
  const [urlInput, setUrlInput] = useState(initial?.polymarket_url || '')
  const [searching, setSearching] = useState(false)
  const [searchErr, setSearchErr] = useState('')

  // Result state
  const [subMarkets, setSubMarkets] = useState<SubMarket[] | null>(null)  // null = not searched yet
  const [eventTitle, setEventTitle] = useState('')
  const [chosen, setChosen] = useState<SubMarket | null>(null)

  // Config state
  const [cfg, setCfg] = useState({ ...DEFAULT_CFG, ...( initial ? {
    side: initial.side,
    price_min: initial.price_min,
    price_max: initial.price_max,
    number_of_lots: initial.number_of_lots,
    sl_ticks: initial.sl_ticks,
    spread_from_ask: initial.spread_from_ask,
  } : {}) })

  const [saving, setSaving] = useState(false)
  const priceMinEditingRef = useRef(false)
  const priceMaxEditingRef = useRef(false)
  const [priceMinInput, setPriceMinInput] = useState(formatPriceCents(cfg.price_min))
  const [priceMaxInput, setPriceMaxInput] = useState(formatPriceCents(cfg.price_max))

  // Pre-populate if editing
  useEffect(() => {
    if (initial?.polymarket_url) {
      // show existing market as "chosen" without searching
      setChosen({
        question: initial.question || '',
        slug: extractSlug(initial.polymarket_url),
        url: initial.polymarket_url,
        condition_id: '', token_id_yes: '', token_id_no: '',
        tick_size: '0.001', neg_risk: false, active: true, closed: false,
      })
    }
  }, [])

  useEffect(() => {
    if (!priceMinEditingRef.current) {
      setPriceMinInput(formatPriceCents(cfg.price_min))
    }
  }, [cfg.price_min])

  useEffect(() => {
    if (!priceMaxEditingRef.current) {
      setPriceMaxInput(formatPriceCents(cfg.price_max))
    }
  }, [cfg.price_max])

  function commitPriceMin() {
    priceMinEditingRef.current = false
    const parsed = parseCentsInput(priceMinInput)
    if (parsed == null) {
      setPriceMinInput(formatPriceCents(cfg.price_min))
      return
    }
    const cents = clampCents(parsed)
    const price = round3(cents / 100)
    setCfg(c => ({ ...c, price_min: price }))
    setPriceMinInput(formatPriceCents(price))
  }

  function commitPriceMax() {
    priceMaxEditingRef.current = false
    const parsed = parseCentsInput(priceMaxInput)
    if (parsed == null) {
      setPriceMaxInput(formatPriceCents(cfg.price_max))
      return
    }
    const cents = clampCents(parsed)
    const price = round3(cents / 100)
    setCfg(c => ({ ...c, price_max: price }))
    setPriceMaxInput(formatPriceCents(price))
  }

  async function search() {
    const raw = urlInput.trim()
    if (!raw) return
    setSearching(true); setSearchErr(''); setSubMarkets(null); setChosen(null)

    try {
      if (isSubMarketUrl(raw)) {
        // Direct sub-market URL → extract event slug (first segment after /event/), then find the sub-market
        const eventSlug = extractEventSlug(raw)
        const subMarketSlug = extractSlug(raw)
        const data: any = await api.admin.resolveEvent(eventSlug)
        const match = data.sub_markets?.find((m: SubMarket) => m.slug === subMarketSlug)
        if (match) {
          setChosen(match)
          setSubMarkets(null)
        } else {
          // sub-market slug didn't match exactly — show all
          setEventTitle(data.title || '')
          setSubMarkets(data.sub_markets || [])
        }
      } else {
        // Event URL or slug → list sub-markets
        const slug = raw.includes('/') ? extractSlug(raw) : raw
        const data: any = await api.admin.resolveEvent(slug)
        setEventTitle(data.title || '')
        const markets = data.sub_markets || []
        if (markets.length === 1) {
          setChosen(markets[0])
        } else {
          setSubMarkets(markets)
        }
      }
    } catch (e: any) {
      setSearchErr(e.message || 'Not found')
    } finally {
      setSearching(false)
    }
  }

  async function save() {
    const url = chosen?.url || urlInput.trim()
    if (!url) { addToast({ type: 'error', message: 'No market selected' }); return }
    setSaving(true)
    try {
      await onSave({
        polymarket_url: url,
        question: chosen?.question || null,
        ...cfg,
      })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally { setSaving(false) }
  }

  // Determine which token to show orderbook for
  const obTokenId = chosen
    ? (cfg.side === 'YES' ? chosen.token_id_yes : chosen.token_id_no)
    : ''

  return (
    <div className="rounded-lg border border-white/10 bg-[#0d0e10] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.06] bg-white/[0.02]">
        <span className="text-xs font-medium text-white/60">Market — <span className="text-brand">{label}</span></span>
        <button className="text-white/30 hover:text-white/60 text-xs leading-none" onClick={onCancel}>✕</button>
      </div>

      <div className="p-4 space-y-3">
        {/* URL search */}
        <div>
          <div className="label-dense mb-1 text-white/50">Polymarket URL <span className="text-white/25">(event or sub-market)</span></div>
          <div className="flex gap-2">
            <input
              className="input-sm flex-1 font-mono text-[10px]"
              placeholder="https://polymarket.com/event/..."
              value={urlInput}
              onChange={e => { setUrlInput(e.target.value); setSearchErr('') }}
              onKeyDown={e => e.key === 'Enter' && search()}
            />
            <button
              className="btn-ghost text-xs px-3 flex-shrink-0"
              onClick={search}
              disabled={searching}
            >
              {searching ? '…' : 'Search'}
            </button>
          </div>
          {searchErr && <div className="text-red-400 text-[10px] mt-1">{searchErr}</div>}
        </div>

        {/* Sub-market list */}
        {subMarkets && (
          <div>
            {eventTitle && <div className="label-dense text-white/40 mb-2">{eventTitle} — {subMarkets.length} markets</div>}
            <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
              {subMarkets.map(sm => (
                <button
                  key={sm.slug}
                  disabled={sm.closed}
                  onClick={() => { setChosen(sm); setSubMarkets(null) }}
                  className={cls(
                    'w-full text-left px-3 py-2 rounded border transition-colors',
                    sm.closed
                      ? 'border-white/5 opacity-40 cursor-not-allowed'
                      : chosen?.slug === sm.slug
                        ? 'border-brand/50 bg-brand/5'
                        : 'border-white/[0.06] hover:border-white/20 hover:bg-white/[0.03]'
                  )}
                >
                  <div className="text-xs text-white/75 leading-snug">{sm.question}</div>
                  <div className="flex gap-2 mt-0.5 text-[10px] text-white/30">
                    {sm.closed && <span className="text-red-400">closed</span>}
                    <span>tick {sm.tick_size}</span>
                    {sm.neg_risk && <span className="text-orange-400">neg-risk</span>}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Chosen market summary */}
        {chosen && (
          <div className="rounded border border-brand/20 bg-brand/5 px-3 py-2 flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-xs text-white/80 leading-snug truncate">{chosen.question || chosen.url}</div>
              <div className="text-[10px] text-white/30 mt-0.5 font-mono truncate">{chosen.url}</div>
            </div>
            <button
              className="text-[10px] text-white/30 hover:text-white/60 flex-shrink-0 mt-0.5"
              onClick={() => { setChosen(null); setSubMarkets(null) }}
            >
              change
            </button>
          </div>
        )}

        {/* Live orderbook */}
        {obTokenId && (
          <OrderbookWidget tokenId={obTokenId} side={cfg.side} />
        )}

        {/* Config */}
        <div className="border-t border-white/[0.05] pt-3 grid grid-cols-2 gap-3">
          <div>
            <div className="label-dense mb-1">Side</div>
            <select className="input-sm w-full" value={cfg.side} onChange={e => setCfg(c => ({ ...c, side: e.target.value }))}>
              <option value="YES">YES</option>
              <option value="NO">NO</option>
            </select>
          </div>
          <div>
            <div className="label-dense mb-1">Lots <span className="text-white/25">(price levels)</span></div>
            <input className="input-sm w-full" type="number" min={1} max={20} value={cfg.number_of_lots}
              onChange={e => setCfg(c => ({ ...c, number_of_lots: +e.target.value }))} />
          </div>
          <div>
            <div className="label-dense mb-1">Price Min</div>
            <input
              className="input-sm w-full"
              type="number"
              step="0.1"
              min={MIN_PRICE_CENTS}
              max={MAX_PRICE_CENTS}
              value={priceMinInput}
              onFocus={() => { priceMinEditingRef.current = true }}
              onChange={e => setPriceMinInput(e.target.value)}
              onBlur={commitPriceMin}
            />
          </div>
          <div>
            <div className="label-dense mb-1">Price Max</div>
            <input
              className="input-sm w-full"
              type="number"
              step="0.1"
              min={MIN_PRICE_CENTS}
              max={MAX_PRICE_CENTS}
              value={priceMaxInput}
              onFocus={() => { priceMaxEditingRef.current = true }}
              onChange={e => setPriceMaxInput(e.target.value)}
              onBlur={commitPriceMax}
            />
          </div>
          <div>
            <div className="label-dense mb-1">SL ticks <span className="text-white/25">(0 = off)</span></div>
            <input className="input-sm w-full" type="number" min={0} value={cfg.sl_ticks}
              onChange={e => setCfg(c => ({ ...c, sl_ticks: +e.target.value }))} />
          </div>
          <div className="flex items-end pb-0.5">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" className="accent-brand" checked={cfg.spread_from_ask}
                onChange={e => setCfg(c => ({ ...c, spread_from_ask: e.target.checked }))} />
              <span className="label-dense">Spread from ask</span>
            </label>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-ghost text-xs" onClick={onCancel}>Cancel</button>
          <button className="btn-primary text-xs" disabled={saving} onClick={save}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Slot row ──────────────────────────────────────────────────────────────────

function diffSlot(draft: Slot, pub: any): { field: string; from: string; to: string }[] {
  const diffs: { field: string; from: string; to: string }[] = []
  if (pub.allocation_pct !== draft.allocation_pct)
    diffs.push({ field: 'alloc', from: `${pub.allocation_pct}%`, to: `${draft.allocation_pct}%` })
  const dm = draft.market
  const pm = pub.market
  if (!pm && dm) {
    diffs.push({ field: 'market', from: '—', to: dm.question || dm.polymarket_url })
  } else if (pm && !dm) {
    diffs.push({ field: 'market', from: pm.question || pm.polymarket_url, to: '—' })
  } else if (pm && dm) {
    if (pm.number_of_lots !== dm.number_of_lots)
      diffs.push({ field: 'lots', from: String(pm.number_of_lots), to: String(dm.number_of_lots) })
    if (pm.side !== dm.side)
      diffs.push({ field: 'side', from: pm.side, to: dm.side })
    if (Math.abs(pm.price_min - dm.price_min) > 0.0001)
      diffs.push({ field: 'min', from: `${formatPriceCents(pm.price_min)}¢`, to: `${formatPriceCents(dm.price_min)}¢` })
    if (Math.abs(pm.price_max - dm.price_max) > 0.0001)
      diffs.push({ field: 'max', from: `${formatPriceCents(pm.price_max)}¢`, to: `${formatPriceCents(dm.price_max)}¢` })
    if (pm.sl_ticks !== dm.sl_ticks)
      diffs.push({ field: 'sl', from: `${pm.sl_ticks}t`, to: `${dm.sl_ticks}t` })
    if (pm.spread_from_ask !== dm.spread_from_ask)
      diffs.push({ field: 'ask', from: String(pm.spread_from_ask), to: String(dm.spread_from_ask) })
    if (pm.polymarket_url !== dm.polymarket_url)
      diffs.push({ field: 'market', from: pm.question || pm.polymarket_url, to: dm.question || dm.polymarket_url })
  }
  return diffs
}

// Derive slot health badge from best bid vs price range
function slotHealthBadge(market: SlotMarket, bestBid: number | null): { label: string; cls: string } | null {
  if (bestBid === null) return null
  const min = market.price_min
  const max = market.price_max
  if (bestBid >= 0.999) return { label: 'ALREADY MAX', cls: 'bg-orange-500/20 text-orange-400' }
  if (bestBid < min || bestBid > max) return { label: 'OUT OF RANGE', cls: 'bg-yellow-500/20 text-yellow-400' }
  return null
}

function SlotRow({ slot, onRefresh, published }: { slot: Slot; onRefresh: () => void; published: any | null }) {
  const addToast = useAppStore(s => s.addToast)
  const [editing, setEditing] = useState(false)
  const [showOb, setShowOb]   = useState(false)
  const [obBestBid, setObBestBid] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [allocEdit, setAllocEdit] = useState(false)
  const [allocVal, setAllocVal]   = useState(String(slot.allocation_pct))
  const allocRef = useRef<HTMLInputElement>(null)
  const [labelEdit, setLabelEdit] = useState(false)
  const [labelVal, setLabelVal]   = useState(slot.label)
  const labelRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (allocEdit) allocRef.current?.select()
  }, [allocEdit])

  useEffect(() => {
    if (labelEdit) labelRef.current?.select()
  }, [labelEdit])

  async function save(m: SlotMarket) {
    await api.admin.setSlotMarket(slot.id, { ...m, resolve_now: true })
    addToast({ type: 'success', message: 'Market saved' })
    setEditing(false)
    onRefresh()
  }

  async function commitAlloc() {
    setAllocEdit(false)
    const v = parseFloat(allocVal)
    if (!isFinite(v) || v <= 0 || v === slot.allocation_pct) {
      setAllocVal(String(slot.allocation_pct))
      return
    }
    try {
      await api.admin.patchSlot(slot.id, { allocation_pct: v })
      onRefresh()
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
      setAllocVal(String(slot.allocation_pct))
    }
  }

  async function commitLabel() {
    setLabelEdit(false)
    const trimmed = labelVal.trim()
    if (!trimmed || trimmed === slot.label) {
      setLabelVal(slot.label)
      return
    }
    try {
      await api.admin.patchSlot(slot.id, { label: trimmed })
      onRefresh()
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
      setLabelVal(slot.label)
    }
  }

  async function handleDelete() {
    setDeleting(true)
    try {
      await api.admin.deleteSlot(slot.id)
      addToast({ type: 'success', message: `Slot "${slot.label}" deleted` })
      onRefresh()
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
      setConfirmDelete(false)
    } finally {
      setDeleting(false)
    }
  }

  const diffs = published ? diffSlot(slot, published) : []
  const isNew = !published
  const hasDiff = isNew || diffs.length > 0
  const badge = slot.market ? slotHealthBadge(slot.market, obBestBid) : null

  // Resolve token id for OB from slot market data
  const obTokenId = slot.market
    ? (slot.market.side === 'YES' ? (slot.market.token_id_yes ?? '') : (slot.market.token_id_no ?? ''))
    : ''

  // Background fetch best bid always (for badge), independent of OB panel
  useEffect(() => {
    if (!obTokenId) return
    let cancelled = false
    async function fetchBid() {
      try {
        const data: any = await api.admin.getBook(obTokenId)
        if (!cancelled) setObBestBid(data?.bids?.[0]?.price ?? null)
      } catch { if (!cancelled) setObBestBid(null) }
    }
    fetchBid()
    const iv = setInterval(fetchBid, 5000)
    return () => { cancelled = true; clearInterval(iv) }
  }, [obTokenId])

  return (
    <div className="border-b border-white/[0.04] last:border-0">
      {/* Row 1: dot + label/alloc + market info */}
      <div className="flex items-start gap-3 px-4 pt-3 pb-1 hover:bg-white/[0.015] transition-colors">
        <div className={cls('w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1', slot.enabled ? 'bg-green-500' : 'bg-white/20')} />
        <div className="w-24 flex-shrink-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            {labelEdit ? (
              <input
                ref={labelRef}
                className="w-20 bg-transparent border-b border-brand/50 text-xs font-medium text-white/80 focus:outline-none"
                value={labelVal}
                onChange={e => setLabelVal(e.target.value)}
                onBlur={commitLabel}
                onKeyDown={e => { if (e.key === 'Enter') commitLabel(); if (e.key === 'Escape') { setLabelEdit(false); setLabelVal(slot.label) } }}
              />
            ) : (
              <div
                className="text-xs font-medium text-white/80 cursor-pointer hover:text-white transition-colors"
                onClick={() => { setLabelEdit(true); setLabelVal(slot.label) }}
                title="Click to rename"
              >
                {slot.label}
              </div>
            )}
            {isNew && <span className="text-[8px] px-1 py-0.5 rounded bg-brand/15 text-brand font-semibold uppercase tracking-wider">new</span>}
            {!isNew && hasDiff && <span className="text-[8px] px-1 py-0.5 rounded bg-amber-400/15 text-amber-400 font-semibold uppercase tracking-wider">modified</span>}
            {badge && (
              <span className={`text-[8px] px-1 py-0.5 rounded font-semibold uppercase tracking-wider ${badge.cls}`}>{badge.label}</span>
            )}
          </div>
          {allocEdit ? (
            <input
              ref={allocRef}
              className="w-16 bg-transparent border-b border-brand/50 text-[10px] font-mono text-white/70 focus:outline-none tabular-nums"
              value={allocVal}
              onChange={e => setAllocVal(e.target.value)}
              onBlur={commitAlloc}
              onKeyDown={e => { if (e.key === 'Enter') commitAlloc(); if (e.key === 'Escape') { setAllocEdit(false); setAllocVal(String(slot.allocation_pct)) } }}
            />
          ) : (
            <div
              className="text-[10px] text-white/30 cursor-pointer hover:text-white/60 transition-colors"
              onClick={() => { setAllocEdit(true); setAllocVal(String(slot.allocation_pct)) }}
              title="Click to edit"
            >
              {slot.allocation_pct}% alloc
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          {slot.market ? (
            <div>
              <div className="text-[11px] text-white/60 truncate">{slot.market.question || slot.market.polymarket_url}</div>
              <div className="flex gap-2 mt-0.5 text-[10px] text-white/30 flex-wrap">
                <span className={slot.market.side === 'YES' ? 'text-green-400' : 'text-red-400'}>{slot.market.side}</span>
                <span>{formatPriceCents(slot.market.price_min)}–{formatPriceCents(slot.market.price_max)}¢</span>
                {obBestBid !== null && (
                  <span className="font-mono">bid <span className="text-white/60">{(obBestBid * 100).toFixed(1)}¢</span></span>
                )}
                <span>{slot.market.number_of_lots} lots</span>
                {slot.market.sl_ticks > 0 && <span>SL {slot.market.sl_ticks}t</span>}
                {slot.market.spread_from_ask && <span className="text-orange-400/70">ask</span>}
              </div>
            </div>
          ) : (
            <span className="text-[10px] text-white/20 italic">No market</span>
          )}
          {diffs.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
              {diffs.map(d => (
                <span key={d.field} className="text-[9px] font-mono">
                  <span className="text-white/20">{d.field} </span>
                  <span className="text-red-400/60 line-through">{d.from}</span>
                  <span className="text-white/20"> → </span>
                  <span className="text-emerald-400/70">{d.to}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Row 2: action buttons — always visible */}
      <div className="flex items-center gap-1 px-8 pb-2.5">
        {confirmDelete ? (
          <>
            <span className="text-[10px] text-red-400/80 mr-1">Delete?</span>
            <button className="text-[10px] px-2 py-0.5 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors disabled:opacity-40"
              onClick={handleDelete} disabled={deleting}>
              {deleting ? '…' : 'Yes'}
            </button>
            <button className="text-[10px] px-2 py-0.5 rounded text-white/30 hover:text-white/60 transition-colors"
              onClick={() => setConfirmDelete(false)}>
              No
            </button>
          </>
        ) : (
          <>
            {hasDiff && !isNew && (
              <button
                className="text-[10px] px-2 py-0.5 rounded text-amber-400/50 hover:text-amber-400 hover:bg-amber-400/10 transition-colors"
                onClick={async () => {
                  try { await api.admin.revertSlot(slot.id); onRefresh() }
                  catch (e: any) { addToast({ type: 'error', message: e.message }) }
                }}
              >
                revert
              </button>
            )}
            {slot.market && (
              <button
                className={cls('text-[10px] px-2 py-0.5 rounded transition-colors',
                  showOb ? 'bg-white/[0.08] text-white/60' : 'text-white/20 hover:text-white/55 hover:bg-white/[0.05]'
                )}
                onClick={() => { setShowOb(v => !v); setEditing(false) }}
              >
                OB
              </button>
            )}
            <button className="btn-ghost text-[10px]" onClick={() => { setEditing(e => !e); setShowOb(false) }}>
              {editing ? 'close' : slot.market ? 'edit' : '+ market'}
            </button>
            <button className="text-[10px] px-2 py-0.5 rounded text-white/20 hover:text-red-400 hover:bg-red-500/10 transition-colors"
              onClick={() => { setConfirmDelete(true); setEditing(false); setShowOb(false) }}>
              ✕
            </button>
          </>
        )}
      </div>
      {showOb && obTokenId && (
        <div className="px-4 pb-3">
          <OrderbookWidget tokenId={obTokenId} side={slot.market!.side} onBestBid={setObBestBid} />
        </div>
      )}
      {editing && (
        <div className="px-4 pb-4">
          <MarketPanel
            initial={slot.market}
            label={slot.label}
            onSave={save}
            onCancel={() => setEditing(false)}
          />
        </div>
      )}
    </div>
  )
}

// ── Global Strategy detail ────────────────────────────────────────────────────

function StrategyDetail({ onRefreshList }: { onRefreshList?: () => void }) {
  const addToast = useAppStore(s => s.addToast)
  const [s, setS]       = useState<Strategy | null>(null)
  const [tab, setTab]   = useState<'slots' | 'volume' | 'settings' | 'versions'>('slots')
  const [slotVol, setSlotVol] = useState<any[]>([])
  const [pausing, setPausing] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [pubKind, setPubKind]   = useState<'minor' | 'major'>('minor')

  // Add slot inline state
  const [addingSlot, setAddingSlot] = useState(false)
  const [newSlotLabel, setNewSlotLabel] = useState('')
  const [newSlotAlloc, setNewSlotAlloc] = useState(50)
  const [newSlotUrl, setNewSlotUrl]     = useState('')
  const [newSlotSearching, setNewSlotSearching] = useState(false)
  const [newSlotSubMarkets, setNewSlotSubMarkets] = useState<SubMarket[] | null>(null)
  const [newSlotChosen, setNewSlotChosen] = useState<SubMarket | null>(null)
  const [newSlotErr, setNewSlotErr] = useState('')
  const [newSlotCfg, setNewSlotCfg] = useState({ ...DEFAULT_CFG })
  const newPriceMinEditingRef = useRef(false)
  const newPriceMaxEditingRef = useRef(false)
  const [newPriceMinInput, setNewPriceMinInput] = useState(formatPriceCents(newSlotCfg.price_min))
  const [newPriceMaxInput, setNewPriceMaxInput] = useState(formatPriceCents(newSlotCfg.price_max))
  const [creatingSlot, setCreatingSlot] = useState(false)

  // Settings
  const [settings, setSettings] = useState({ name: '', reserve_pct: 5, fee_side: 'none', new_user_free_fee_days: 0 })
  const [savingSettings, setSavingSettings] = useState(false)
  const [publishedSlots, setPublishedSlots] = useState<Record<string, any>>({})  // slot_id → published slot data
  const [freeFeeUsers, setFreeFeeUsers] = useState<import('../../types').AdminUser[]>([])

  const loadFreeFeeUsers = useCallback(() => {
    (api.admin.listUsers() as Promise<any[]>).then((users: any[]) => {
      const now = Date.now()
      setFreeFeeUsers(users.filter((u: any) => u.free_fee_ends_at && new Date(u.free_fee_ends_at).getTime() > now))
    }).catch(() => {})
  }, [])

  const load = useCallback(() => {
    Promise.all([
      api.admin.getGlobalStrategy(),
      api.admin.getPublishedSlots().catch(() => null),
    ]).then(([d, pub]: any[]) => {
      setS(d)
      setSettings({ name: d.name, reserve_pct: d.reserve_pct, fee_side: d.fee_side, new_user_free_fee_days: d.new_user_free_fee_days ?? 0 })
      if (pub?.slots?.length) {
        const map: Record<string, any> = {}
        for (const sl of pub.slots) map[sl.id] = sl
        setPublishedSlots(map)
      }
    }).catch((e: any) => {
      console.error('[global strategy] load error', e)
    })
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadFreeFeeUsers() }, [loadFreeFeeUsers])

  useEffect(() => {
    if (tab === 'volume') {
      api.admin.getSlotVolumeStats().then((d: any) => setSlotVol(d ?? [])).catch(() => {})
      const iv = setInterval(() => {
        api.admin.getSlotVolumeStats().then((d: any) => setSlotVol(d ?? [])).catch(() => {})
      }, 5000)
      return () => clearInterval(iv)
    }
  }, [tab])

  async function togglePause() {
    setPausing(true)
    try {
      await api.admin.setGlobalPause(s!.id, !s!.global_pause)
      addToast({ type: 'success', message: s!.global_pause ? 'Unpaused' : 'Paused' })
      load()
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setPausing(false) }
  }

  async function publish() {
    setPublishing(true)
    try {
      await api.admin.publishVersion({ kind: pubKind })
      addToast({ type: 'success', message: 'Version published' })
      load(); onRefreshList?.()
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setPublishing(false) }
  }

  async function saveSettings() {
    setSavingSettings(true)
    try {
      await api.admin.updateStrategy(settings)
      addToast({ type: 'success', message: 'Saved' })
      load(); onRefreshList?.()
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setSavingSettings(false) }
  }

  async function searchNewSlot() {
    const raw = newSlotUrl.trim()
    if (!raw) return
    setNewSlotSearching(true); setNewSlotErr(''); setNewSlotSubMarkets(null); setNewSlotChosen(null)
    try {
      if (isSubMarketUrl(raw)) {
        const eventSlug = extractEventSlug(raw)
        const subMarketSlug = extractSlug(raw)
        const data: any = await api.admin.resolveEvent(eventSlug)
        const markets = data.sub_markets || []
        const match = markets.find((m: SubMarket) => m.slug === subMarketSlug)
        if (match) { setNewSlotChosen(match) }
        else { setNewSlotSubMarkets(markets); setNewSlotErr('Sub-market not found, showing all') }
      } else {
        const slug = raw.includes('/') ? extractSlug(raw) : raw
        const data: any = await api.admin.resolveEvent(slug)
        const markets = data.sub_markets || []
        if (markets.length === 1) { setNewSlotChosen(markets[0]) }
        else { setNewSlotSubMarkets(markets) }
      }
    } catch (e: any) { setNewSlotErr(e.message || 'Not found') }
    finally { setNewSlotSearching(false) }
  }

  async function createSlotWithMarket() {
    if (!newSlotLabel) { addToast({ type: 'error', message: 'Slot name required' }); return }
    const url = newSlotChosen?.url || newSlotUrl.trim()
    if (!url) { addToast({ type: 'error', message: 'Market required' }); return }
    setCreatingSlot(true)
    try {
      const slotRes: any = await api.admin.createSlot({ label: newSlotLabel, allocation_pct: newSlotAlloc, order_index: s!.slots.length })
      await api.admin.setSlotMarket(slotRes.id, {
        polymarket_url: url,
        question: newSlotChosen?.question || null,
        resolve_now: true,
        ...newSlotCfg,
      })
      addToast({ type: 'success', message: `Slot "${newSlotLabel}" created` })
      setAddingSlot(false)
      setNewSlotLabel(''); setNewSlotAlloc(50); setNewSlotUrl(''); setNewSlotChosen(null); setNewSlotSubMarkets(null)
      setNewSlotCfg({ ...DEFAULT_CFG })
      load()
    } catch (e: any) { addToast({ type: 'error', message: e.message }) }
    finally { setCreatingSlot(false) }
  }

  const newSlotObTokenId = newSlotChosen
    ? (newSlotCfg.side === 'YES' ? newSlotChosen.token_id_yes : newSlotChosen.token_id_no)
    : ''

  useEffect(() => {
    if (!newPriceMinEditingRef.current) {
      setNewPriceMinInput(formatPriceCents(newSlotCfg.price_min))
    }
  }, [newSlotCfg.price_min])

  useEffect(() => {
    if (!newPriceMaxEditingRef.current) {
      setNewPriceMaxInput(formatPriceCents(newSlotCfg.price_max))
    }
  }, [newSlotCfg.price_max])

  if (!s) return <div className="flex items-center justify-center h-40 text-white/20 text-xs">Loading…</div>

  const totalAlloc = s.slots.reduce((sum, sl) => sum + sl.allocation_pct, 0)

  function commitNewPriceMin() {
    newPriceMinEditingRef.current = false
    const parsed = parseCentsInput(newPriceMinInput)
    if (parsed == null) {
      setNewPriceMinInput(formatPriceCents(newSlotCfg.price_min))
      return
    }
    const cents = clampCents(parsed)
    const price = round3(cents / 100)
    setNewSlotCfg(c => ({ ...c, price_min: price }))
    setNewPriceMinInput(formatPriceCents(price))
  }

  function commitNewPriceMax() {
    newPriceMaxEditingRef.current = false
    const parsed = parseCentsInput(newPriceMaxInput)
    if (parsed == null) {
      setNewPriceMaxInput(formatPriceCents(newSlotCfg.price_max))
      return
    }
    const cents = clampCents(parsed)
    const price = round3(cents / 100)
    setNewSlotCfg(c => ({ ...c, price_max: price }))
    setNewPriceMaxInput(formatPriceCents(price))
  }

  const SLOT_COLORS = ['bg-brand', 'bg-purple-500', 'bg-orange-400', 'bg-teal-400', 'bg-pink-400', 'bg-yellow-400']

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-semibold text-white/90">{s.name}</h2>
            {s.global_pause && <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-400">PAUSED</span>}
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 font-mono">published v{s.version}</span>
            <span className="text-[10px] text-white/20">·</span>
            <span className="text-[10px] text-white/25">Reserve {s.reserve_pct}%</span>
            <span className="text-[10px] text-white/20">·</span>
            <span className="text-[10px] text-white/25">Fee: {s.fee_side}</span>
            {(() => {
              const minDraft = computeMinCapital(s.slots, s.reserve_pct)
              const minPub = s.min_capital_published
              return (<>
                {minDraft > 0 && (
                  <>
                    <span className="text-[10px] text-white/20">·</span>
                    <span className="text-[10px] text-white/25">
                      Min draft <span className="font-mono text-white/50">${minDraft.toFixed(0)}</span>
                    </span>
                  </>
                )}
                {minPub != null && minPub > 0 && minPub !== minDraft && (
                  <>
                    <span className="text-[10px] text-white/20">·</span>
                    <span className="text-[10px] text-white/20">
                      published <span className="font-mono">${minPub.toFixed(0)}</span>
                    </span>
                  </>
                )}
              </>)
            })()}
          </div>
        </div>
        <button
          className={cls('btn-ghost text-xs flex-shrink-0', s.global_pause && 'text-orange-400 border-orange-500/30')}
          disabled={pausing} onClick={togglePause}
        >
          {pausing ? '…' : s.global_pause ? 'Unpause' : 'Pause all'}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-white/[0.06] gap-1">
        {(['slots', 'volume', 'settings', 'versions'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cls('px-4 py-2 text-[11px] font-medium capitalize transition-colors',
              tab === t ? 'text-brand border-b-2 border-brand' : 'text-white/40 hover:text-white/60'
            )}
          >
            {t === 'slots' ? `Slots (${s.slots.length})` : t === 'versions' ? `History (${s.versions.length})` : t === 'volume' ? 'Volume' : 'Settings'}
          </button>
        ))}
      </div>

      {/* ── SLOTS TAB ── */}
      {tab === 'slots' && (
        <div className="space-y-3">
          {/* Alloc bar */}
          {s.slots.length > 0 && (
            <div className="panel px-4 py-3">
              <div className="flex justify-between mb-1.5">
                <span className="label-dense">Allocation</span>
                <span className={cls('text-[10px] font-mono', Math.abs(totalAlloc - 100) < 0.01 ? 'text-green-400' : 'text-orange-400')}>
                  {totalAlloc.toFixed(0)}%
                </span>
              </div>
              <div className="flex rounded overflow-hidden h-1.5 gap-px">
                {s.slots.map((sl, i) => (
                  <div key={sl.id} className={cls(SLOT_COLORS[i % SLOT_COLORS.length], !sl.enabled && 'opacity-30')}
                    style={{ width: `${sl.allocation_pct}%` }} title={`${sl.label}: ${sl.allocation_pct}%`} />
                ))}
              </div>
            </div>
          )}

          {/* Slot list */}
          <div className="panel overflow-hidden">
            <div className="px-4 py-2.5 border-b border-white/[0.04] flex items-center justify-between">
              <span className="label-dense">Slots</span>
              <button className="btn-ghost text-[10px]" onClick={() => setAddingSlot(v => !v)}>
                {addingSlot ? '✕ Cancel' : '+ Add slot'}
              </button>
            </div>

            {/* Inline new slot form */}
            {addingSlot && (
              <div className="border-b border-white/[0.04] p-4 bg-white/[0.02] space-y-3">
                <div className="text-[11px] font-medium text-white/60">New slot</div>

                {/* Name + alloc */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="label-dense mb-1">Slot name</div>
                    <input className="input-sm w-full" placeholder="e.g. Slot A" value={newSlotLabel}
                      onChange={e => setNewSlotLabel(e.target.value)} />
                  </div>
                  <div>
                    <div className="label-dense mb-1">Allocation %</div>
                    <input className="input-sm w-full" type="number" min={1} max={100} value={newSlotAlloc}
                      onChange={e => setNewSlotAlloc(+e.target.value)} />
                  </div>
                </div>

                {/* Market URL search */}
                <div>
                  <div className="label-dense mb-1 text-white/50">Market URL</div>
                  <div className="flex gap-2">
                    <input className="input-sm flex-1 font-mono text-[10px]"
                      placeholder="https://polymarket.com/event/..."
                      value={newSlotUrl}
                      onChange={e => { setNewSlotUrl(e.target.value); setNewSlotErr('') }}
                      onKeyDown={e => e.key === 'Enter' && searchNewSlot()}
                    />
                    <button className="btn-ghost text-xs px-3" onClick={searchNewSlot} disabled={newSlotSearching}>
                      {newSlotSearching ? '…' : 'Search'}
                    </button>
                  </div>
                  {newSlotErr && <div className="text-[10px] text-red-400 mt-1">{newSlotErr}</div>}
                </div>

                {/* Sub-market list */}
                {newSlotSubMarkets && (
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {newSlotSubMarkets.map(sm => (
                      <button key={sm.slug} disabled={sm.closed}
                        onClick={() => { setNewSlotChosen(sm); setNewSlotSubMarkets(null) }}
                        className={cls('w-full text-left px-3 py-2 rounded border transition-colors text-xs',
                          sm.closed ? 'opacity-40 cursor-not-allowed border-white/5'
                            : 'border-white/[0.06] hover:border-white/20 hover:bg-white/[0.03]'
                        )}
                      >
                        {sm.question}
                        {sm.closed && <span className="ml-2 text-red-400 text-[10px]">closed</span>}
                      </button>
                    ))}
                  </div>
                )}

                {/* Chosen */}
                {newSlotChosen && (
                  <div className="rounded border border-brand/20 bg-brand/5 px-3 py-2 flex items-center justify-between gap-2">
                    <div className="text-xs text-white/75 truncate">{newSlotChosen.question}</div>
                    <button className="text-[10px] text-white/30 hover:text-white/60 flex-shrink-0"
                      onClick={() => { setNewSlotChosen(null); setNewSlotSubMarkets(null) }}>change</button>
                  </div>
                )}

                {/* Orderbook */}
                {newSlotObTokenId && (
                  <OrderbookWidget tokenId={newSlotObTokenId} side={newSlotCfg.side} />
                )}

                {/* Config */}
                <div className="grid grid-cols-2 gap-3 border-t border-white/[0.05] pt-3">
                  <div>
                    <div className="label-dense mb-1">Side</div>
                    <select className="input-sm w-full" value={newSlotCfg.side}
                      onChange={e => setNewSlotCfg(c => ({ ...c, side: e.target.value }))}>
                      <option value="YES">YES</option>
                      <option value="NO">NO</option>
                    </select>
                  </div>
                  <div>
                    <div className="label-dense mb-1">Lots</div>
                    <input className="input-sm w-full" type="number" min={1} value={newSlotCfg.number_of_lots}
                      onChange={e => setNewSlotCfg(c => ({ ...c, number_of_lots: +e.target.value }))} />
                  </div>
                  <div>
                    <div className="label-dense mb-1">Price Min</div>
                    <input
                      className="input-sm w-full"
                      type="number"
                      step="0.1"
                      min={MIN_PRICE_CENTS}
                      max={MAX_PRICE_CENTS}
                      value={newPriceMinInput}
                      onFocus={() => { newPriceMinEditingRef.current = true }}
                      onChange={e => setNewPriceMinInput(e.target.value)}
                      onBlur={commitNewPriceMin}
                    />
                  </div>
                  <div>
                    <div className="label-dense mb-1">Price Max</div>
                    <input
                      className="input-sm w-full"
                      type="number"
                      step="0.1"
                      min={MIN_PRICE_CENTS}
                      max={MAX_PRICE_CENTS}
                      value={newPriceMaxInput}
                      onFocus={() => { newPriceMaxEditingRef.current = true }}
                      onChange={e => setNewPriceMaxInput(e.target.value)}
                      onBlur={commitNewPriceMax}
                    />
                  </div>
                  <div>
                    <div className="label-dense mb-1">SL ticks</div>
                    <input className="input-sm w-full" type="number" min={0} value={newSlotCfg.sl_ticks}
                      onChange={e => setNewSlotCfg(c => ({ ...c, sl_ticks: +e.target.value }))} />
                  </div>
                  <div className="flex items-end pb-0.5">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="checkbox" className="accent-brand" checked={newSlotCfg.spread_from_ask}
                        onChange={e => setNewSlotCfg(c => ({ ...c, spread_from_ask: e.target.checked }))} />
                      <span className="label-dense">Spread from ask</span>
                    </label>
                  </div>
                </div>

                <button className="btn-primary text-xs w-full" disabled={creatingSlot} onClick={createSlotWithMarket}>
                  {creatingSlot ? 'Creating…' : 'Create slot'}
                </button>
              </div>
            )}

            {s.slots.length === 0 && !addingSlot && (
              <div className="px-4 py-8 text-center text-[11px] text-white/20">
                No slots yet — add one above
              </div>
            )}
            {s.slots.map(slot => <SlotRow key={slot.id} slot={slot} onRefresh={load} published={publishedSlots[slot.id] ?? null} />)}
            {/* Removed slots: in published but not in current draft */}
            {Object.values(publishedSlots)
              .filter(ps => !s.slots.find(sl => sl.id === ps.id))
              .map(ps => (
                <div key={ps.id} className="border-b border-white/[0.04] last:border-0 px-4 py-3 flex items-center gap-3 group hover:bg-white/[0.015] transition-colors">
                  <div className="w-1.5 h-1.5 rounded-full bg-red-500/40 flex-shrink-0" />
                  <div className="w-28 flex-shrink-0">
                    <div className="flex items-center gap-1.5">
                      <div className="text-xs font-medium text-white/30 line-through">{ps.label}</div>
                      <span className="text-[8px] px-1 py-0.5 rounded bg-red-500/15 text-red-400 font-semibold uppercase tracking-wider">removed</span>
                    </div>
                    <div className="text-[10px] text-white/20">{ps.allocation_pct}% alloc</div>
                  </div>
                  <div className="flex-1 min-w-0 opacity-40">
                    {ps.market
                      ? <div className="text-[11px] text-white/40 truncate line-through">{ps.market.question || ps.market.polymarket_url}</div>
                      : <span className="text-[10px] text-white/15 italic">No market</span>}
                  </div>
                  <button
                    className="text-[10px] px-2 py-0.5 rounded text-emerald-400/50 hover:text-emerald-400 hover:bg-emerald-400/10 transition-colors flex-shrink-0 opacity-0 group-hover:opacity-100"
                    onClick={async () => {
                      try { await api.admin.restoreSlot(ps.id); load() }
                      catch (e: any) { console.error(e) }
                    }}
                  >
                    restore
                  </button>
                </div>
              ))}
          </div>

          {/* Publish */}
          <div className="panel px-4 py-3 space-y-2">
            <div className="label-dense">Publish version <span className="text-white/25 font-normal">— freezes current slot config</span></div>
            <div className="flex gap-2 items-end">
              <div>
                <div className="label-dense mb-1">Type</div>
                <select className="input-sm w-20" value={pubKind} onChange={e => setPubKind(e.target.value as any)}>
                  <option value="minor">minor</option>
                  <option value="major">major</option>
                </select>
              </div>
              <button className="btn-primary text-xs" disabled={publishing} onClick={publish}>
                {publishing ? '…' : 'Publish'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── VOLUME TAB ── */}
      {tab === 'volume' && (
        <div className="panel overflow-hidden">
          {slotVol.length === 0 ? (
            <div className="px-4 py-8 text-center text-[11px] text-white/20 animate-pulse">Loading…</div>
          ) : (
            <div className="overflow-x-auto">
              <div className="grid grid-cols-7 gap-2 px-4 py-2 bg-white/[0.015] min-w-[520px]">
                {['Slot', 'Alloc', 'Volume', '×', '24h Vol', 'PnL', 'Fees'].map(h => (
                  <div key={h} className="text-[9px] font-semibold uppercase tracking-widest text-white/20">{h}</div>
                ))}
              </div>
              {slotVol.map((sv: any) => {
                const sessions = s?.slots?.length ?? 0
                return (
                  <div key={sv.slot_id} className="grid grid-cols-7 gap-2 px-4 py-2.5 border-t border-white/[0.04] hover:bg-white/[0.02] min-w-[520px]">
                    <div className="text-[11px] font-mono text-white/60 truncate">{sv.label || sv.slot_id.slice(0, 8)}</div>
                    <div className="text-[10px] font-mono text-white/30">{sv.alloc_pct}%</div>
                    <div className="text-[11px] font-mono text-white/50 tabular-nums">{fmtUsd(sv.total_volume, 0)}</div>
                    <div className="text-[10px] font-mono text-white/30 tabular-nums">
                      {sv.alloc_pct > 0 && sv.total_volume > 0
                        ? `${(sv.total_volume / (sv.alloc_pct)).toFixed(1)}×`
                        : '—'}
                    </div>
                    <div className="text-[10px] font-mono text-white/30 tabular-nums">
                      {sv.daily_volume > 0 ? fmtUsd(sv.daily_volume, 0) : '—'}
                    </div>
                    <div className={`text-[11px] font-mono font-semibold tabular-nums ${pnlColor(sv.total_pnl)}`}>{fmtUsd(sv.total_pnl)}</div>
                    <div className="text-[10px] font-mono text-white/25 tabular-nums">{fmtUsd(sv.total_fees)}</div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── SETTINGS TAB ── */}
      {tab === 'settings' && (
        <div className="panel p-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <div className="label-dense mb-1">Name</div>
              <input className="input-sm w-full" value={settings.name}
                onChange={e => setSettings(s => ({ ...s, name: e.target.value }))} />
            </div>
            <div>
              <div className="label-dense mb-1">Fee reserve <span className="text-white/30">(%)</span></div>
              <input className="input-sm w-full" type="number" step="0.5" min={0} max={100} value={settings.reserve_pct}
                onChange={e => setSettings(s => ({ ...s, reserve_pct: +e.target.value }))} />
              <div className="text-[10px] text-white/25 mt-1">% held back from session capital as USDC buffer for fees</div>
            </div>
            <div>
              <div className="label-dense mb-1">Fee side</div>
              <select className="input-sm w-full" value={settings.fee_side}
                onChange={e => setSettings(s => ({ ...s, fee_side: e.target.value }))}>
                <option value="none">None</option>
                <option value="buy">Buy only</option>
                <option value="sell">Sell only</option>
                <option value="both">Both</option>
              </select>
            </div>
            <div>
              <div className="label-dense mb-1">Free fee period <span className="text-white/30">(days for new users, 0 = disabled)</span></div>
              <input className="input-sm w-full" type="number" step="1" min={0} max={365} value={settings.new_user_free_fee_days}
                onChange={e => setSettings(s => ({ ...s, new_user_free_fee_days: +e.target.value }))} />
            </div>
            {/* Active free-fee users */}
            {freeFeeUsers.length > 0 && (
              <div className="col-span-2">
                <div className="label-dense mb-2">Active free-fee users <span className="text-white/30">({freeFeeUsers.length})</span></div>
                <div className="space-y-1">
                  {freeFeeUsers.map(u => {
                    const daysLeft = Math.max(1, Math.ceil((new Date(u.free_fee_ends_at!).getTime() - Date.now()) / 86_400_000))
                    return (
                      <div key={u.id} className="flex items-center justify-between px-2 py-1 rounded bg-emerald-950/30 border border-emerald-500/10">
                        <span className="text-[11px] text-white/70 font-mono">{u.display_name || u.wallet_address?.slice(0, 10)}</span>
                        <span className="text-[10px] text-emerald-400/80 font-mono">{daysLeft}d left</span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
          <div className="flex justify-end">
            <button className="btn-primary text-xs" disabled={savingSettings} onClick={saveSettings}>
              {savingSettings ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}

      {/* ── VERSIONS TAB ── */}
      {tab === 'versions' && (
        <div className="panel overflow-hidden">
          {s.versions.length === 0 ? (
            <div className="px-4 py-8 text-center text-[11px] text-white/20">No versions yet</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  {['Version', 'Published'].map(h => (
                    <th key={h} className="text-left px-4 py-2 text-[10px] text-white/30 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {s.versions.map((v: any) => (
                  <tr key={v.id} className="border-b border-white/[0.03] hover:bg-white/[0.015]">
                    <td className="px-4 py-2.5 font-mono text-xs text-brand">{v.label}</td>
                    <td className="px-4 py-2.5 text-[11px] text-white/40">{fmtDate(v.published_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page — Global Strategy ───────────────────────────────────────────────

export default function AdminStrategies() {
  return (
    <div className="h-screen overflow-y-auto bg-[#0b0c0e]">
      <div className="px-6 py-5 border-b border-white/[0.06]">
        <h1 className="text-sm font-semibold text-white/70">Global Strategy</h1>
        <p className="text-[10px] text-white/25 mt-0.5">Single platform-wide strategy. All user sessions use this configuration.</p>
      </div>
      <div className="p-5">
        <StrategyDetail />
      </div>
    </div>
  )
}
