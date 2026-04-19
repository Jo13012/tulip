import type { HealthState, SessionStatus } from '../../types'

type Status = HealthState | SessionStatus | string

const statusConfig: Record<string, { dot: string; text: string; label: string }> = {
  running:    { dot: 'bg-success animate-pulse', text: 'text-success',  label: 'Running' },
  starting:   { dot: 'bg-info animate-pulse',    text: 'text-info',     label: 'Starting' },
  idle:       { dot: 'bg-white/30',              text: 'text-white/50', label: 'Idle' },
  recovering: { dot: 'bg-warning animate-pulse', text: 'text-warning',  label: 'Recovering' },
  degraded:   { dot: 'bg-warning',               text: 'text-warning',  label: 'Degraded' },
  stopping:   { dot: 'bg-white/40',              text: 'text-white/50', label: 'Stopping' },
  stopped:    { dot: 'bg-white/20',              text: 'text-white/40', label: 'Stopped' },
  error:      { dot: 'bg-danger',                text: 'text-danger',   label: 'Error' },
  pending:    { dot: 'bg-brand/50',              text: 'text-brand',    label: 'Pending' },
}

interface StatusBadgeProps {
  status: Status
  size?: 'sm' | 'md'
}

export function StatusBadge({ status, size = 'md' }: StatusBadgeProps) {
  const cfg = statusConfig[status] ?? { dot: 'bg-white/20', text: 'text-white/40', label: status }
  const dotSize  = size === 'sm' ? 'w-1.5 h-1.5' : 'w-2 h-2'
  const textSize = size === 'sm' ? 'text-xs' : 'text-sm'

  return (
    <span className={`inline-flex items-center gap-1.5 font-medium ${cfg.text} ${textSize}`}>
      <span className={`rounded-full flex-shrink-0 ${dotSize} ${cfg.dot}`} />
      {cfg.label}
    </span>
  )
}
