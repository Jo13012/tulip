import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { useAppStore } from '../../store'

// ── Section ───────────────────────────────────────────────────────────────────

export function Section({ title, action, children }: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11px] font-semibold text-white/50">{title}</p>
        {action}
      </div>
      <div className="rounded-xl border border-white/[0.07] bg-[#0d0e10] divide-y divide-white/[0.05] overflow-hidden">
        {children}
      </div>
    </div>
  )
}

// ── Row ───────────────────────────────────────────────────────────────────────

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 px-4 py-3 min-h-[48px]">
      <span className="text-[11px] text-white/40 flex-shrink-0 w-28">{label}</span>
      <div className="flex items-center gap-2 min-w-0">
        {children}
      </div>
    </div>
  )
}

// ── Address field with copy (no truncation) ───────────────────────────────────

export function AddressField({ value }: { value: string }) {
  const addToast = useAppStore(s => s.addToast)
  const [copied, setCopied] = useState(false)

  function copy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      addToast({ type: 'success', message: 'Copied' })
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-[11px] font-mono text-white/40 break-all leading-relaxed">{value}</span>
      <button
        onClick={copy}
        title="Copy"
        className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-md text-white/20 hover:text-white/55 hover:bg-white/[0.06] transition-colors"
      >
        {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
      </button>
    </div>
  )
}

// ── Inline text input with optional right button ──────────────────────────────

export function InlineInput(props: React.InputHTMLAttributes<HTMLInputElement> & {
  suffix?: React.ReactNode
}) {
  const { suffix, className, ...rest } = props
  if (!suffix) {
    return (
      <input
        {...rest}
        className={`bg-white/[0.05] border border-white/[0.08] rounded-lg px-3 py-1.5 text-[12px] text-white/75 placeholder-white/20 focus:outline-none focus:border-white/20 focus:bg-white/[0.07] transition-all w-44 ${className ?? ''}`}
      />
    )
  }
  return (
    <div className="inline-flex items-center bg-white/[0.05] border border-white/[0.08] rounded-lg focus-within:border-white/20 focus-within:bg-white/[0.07] transition-all max-w-full">
      <input
        {...rest}
        className={`bg-transparent px-3 py-1.5 text-[12px] text-white/75 placeholder-white/20 focus:outline-none min-w-0 w-32 ${className ?? ''}`}
      />
      <div className="flex-shrink-0 border-l border-white/[0.08]">
        {suffix}
      </div>
    </div>
  )
}

// ── Ghost button ──────────────────────────────────────────────────────────────

export function GhostButton({ children, disabled, onClick, warn, compact }: {
  children: React.ReactNode
  disabled?: boolean
  onClick?: () => void
  warn?: boolean
  compact?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-lg border text-[11px] font-medium transition-colors disabled:opacity-30 disabled:cursor-not-allowed
        ${compact ? 'px-2.5 py-1' : 'px-3 py-1.5'}
        ${warn
          ? 'border-amber-400/20 text-amber-300/70 hover:border-amber-400/40 hover:text-amber-300 bg-amber-500/5'
          : 'border-white/[0.08] text-white/40 hover:border-white/[0.16] hover:text-white/65 bg-transparent'
        }`}
    >
      {children}
    </button>
  )
}

// ── Stat tile (for grids) ─────────────────────────────────────────────────────

export function StatTile({ label, value, sub, action, accent }: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  action?: React.ReactNode
  accent?: string
}) {
  return (
    <div className="bg-[#0d0e10] px-4 py-3.5 space-y-0.5">
      <p className="text-[9px] uppercase tracking-widest font-semibold text-white/25">{label}</p>
      <p className={`text-base font-bold tabular-nums font-mono ${accent ?? 'text-white/75'}`}>{value}</p>
      {sub && <div className="text-[9px] text-white/20 mt-0.5">{sub}</div>}
      {action && <div className="mt-1.5">{action}</div>}
    </div>
  )
}
