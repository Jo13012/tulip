import { motion } from 'framer-motion'
import type { Session } from '../../types'
import { StatusBadge } from '../ui/StatusBadge'
import { EmptyState } from '../ui/EmptyState'
import { fmtDate, fmtUsd } from '../../utils/format'

interface Props { sessions: Session[] }

export function SessionHistory({ sessions }: Props) {
  if (!sessions.length) {
    return <EmptyState icon="◷" title="No session history yet" description="Completed sessions will appear here." />
  }

  return (
    <div className="space-y-2">
      {sessions.map((s, i) => (
        <motion.div
          key={s.session_id}
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: i * 0.03 }}
          className="card-sm flex items-center justify-between gap-4"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <StatusBadge status={s.status} size="sm" />
              <span className="text-xs text-white/30">{fmtDate(s.started_at)}</span>
            </div>
            <div className="text-sm font-medium">
              {fmtUsd(s.gross_capital)} · {s.stop_mode === 'days' ? `${s.stop_days}d` : 'volume'}
            </div>
            {s.stop_reason && (
              <div className="text-xs text-white/30 truncate">{s.stop_reason}</div>
            )}
          </div>
          <div className="text-right text-xs text-white/40 flex-shrink-0">
            {s.stopped_at && fmtDate(s.stopped_at)}
          </div>
        </motion.div>
      ))}
    </div>
  )
}
