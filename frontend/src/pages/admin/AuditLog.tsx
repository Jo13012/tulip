import { useEffect, useState } from 'react'
import { api } from '../../utils/api'
import { fmtDate } from '../../utils/format'

const actionColor: Record<string, string> = {
  'session.launch':           'text-success',
  'session.force_stop':       'text-danger',
  'session.hard_stop':        'text-danger',
  'global.pause':             'text-warning',
  'global.unpause':           'text-info',
  'strategy.publish':         'text-brand',
  'slot.replace_queued':      'text-warning',
  'session.recovery_start':   'text-info',
  'session.recovery_success': 'text-success',
  'session.recovery_failed':  'text-danger',
}

export default function AdminAuditLog() {
  const [logs, setLogs] = useState<any[]>([])

  useEffect(() => {
    api.admin.getAuditLog().then((d: any) => setLogs(d)).catch(() => {})
  }, [])

  return (
    <div className="p-6 space-y-3">
      <h1 className="text-base font-semibold">Audit Log</h1>
      <div className="panel overflow-hidden">
        <table className="w-full">
          <thead><tr>
            <th className="th text-left">Time</th>
            <th className="th text-left">Actor</th>
            <th className="th text-left">Action</th>
            <th className="th text-left">Entity</th>
            <th className="th text-left">Detail</th>
          </tr></thead>
          <tbody>
            {logs.map((l: any) => (
              <tr key={l.id} className="tr-row font-mono">
                <td className="td text-white/30 text-[10px] whitespace-nowrap">{fmtDate(l.created_at)}</td>
                <td className="td text-[10px]">
                  <span className={l.actor_role === 'admin' ? 'text-brand' : l.actor_role === 'system' ? 'text-white/30' : 'text-white/50'}>
                    {l.actor_role}
                  </span>
                </td>
                <td className={`td text-[10px] font-semibold ${actionColor[l.action] ?? 'text-white/50'}`}>{l.action}</td>
                <td className="td text-[10px] text-white/30">
                  {l.entity_type && <span>{l.entity_type}:</span>}
                  {l.entity_id && <span className="text-white/20 ml-1">{l.entity_id?.slice(0, 8)}</span>}
                </td>
                <td className="td text-[10px] text-white/30 max-w-[200px] truncate">{l.detail || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!logs.length && <div className="px-4 py-4 text-[10px] text-white/20 italic">No audit events</div>}
      </div>
    </div>
  )
}
