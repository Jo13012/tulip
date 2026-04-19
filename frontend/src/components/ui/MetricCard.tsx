import { InfoTip } from './Tooltip'

interface MetricCardProps {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  tooltip?: React.ReactNode
  accent?: 'default' | 'success' | 'danger' | 'warning'
  size?: 'sm' | 'md' | 'lg'
}

const accentClass = {
  default: 'text-white',
  success: 'text-success',
  danger:  'text-danger',
  warning: 'text-warning',
}

export function MetricCard({ label, value, sub, tooltip, accent = 'default', size = 'md' }: MetricCardProps) {
  const valClass = `font-bold tabular-nums ${accentClass[accent]} ${
    size === 'lg' ? 'text-3xl' : size === 'sm' ? 'text-xl' : 'text-2xl'
  }`

  return (
    <div className="card-sm flex flex-col gap-1">
      <div className="flex items-center gap-1">
        <span className="label">{label}</span>
        {tooltip && <InfoTip content={tooltip} />}
      </div>
      <div className={valClass}>{value}</div>
      {sub && <div className="text-xs text-white/40">{sub}</div>}
    </div>
  )
}
