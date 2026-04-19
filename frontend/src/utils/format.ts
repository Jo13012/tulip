export function fmtUsd(n: number, decimals = 2): string {
  if (n === undefined || n === null) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

/** Floor at 2 decimal places: 0.0014 → $0.00, 0.005 → $0.01 */
export function fmtUsdFloor(n: number): string {
  if (n === undefined || n === null) return '—'
  const floored = Math.floor(Math.abs(n) * 100) / 100
  const sign = n < 0 ? '-' : ''
  return `${sign}$${floored.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function fmtPct(n: number, decimals = 2): string {
  if (n === undefined || n === null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(decimals)}%`
}

export function fmtStoredBps(n: number, decimals = 1): string {
  if (n === undefined || n === null) return '—'
  return `${(n / 10).toFixed(decimals).replace(/\.?0+$/, '')} bps`
}

export function fmtStoredBpsPct(n: number, decimals = 4): string {
  if (n === undefined || n === null) return '—'
  // DB stores bps×10; divide by 1000 to get %. e.g. 40 → 0.04%
  return `${(n / 1000).toFixed(decimals).replace(/\.?0+$/, '')}%`
}

export function fmtMul(n: number): string {
  if (!n && n !== 0) return '—'
  return `${n.toFixed(2)}x`
}

export function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`
}

export function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function pnlColor(n: number): string {
  if (n > 0) return 'text-success'
  if (n < 0) return 'text-danger'
  return 'text-white/60'
}

export function healthColor(state: string): string {
  switch (state) {
    case 'running':    return 'text-success'
    case 'recovering': return 'text-warning'
    case 'degraded':
    case 'error':      return 'text-danger'
    case 'stopping':
    case 'stopped':    return 'text-white/40'
    default:           return 'text-white/60'
  }
}
