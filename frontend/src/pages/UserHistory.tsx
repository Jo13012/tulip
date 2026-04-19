import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../utils/api'
import type { AppUser, Session } from '../types'
import { SessionHistory } from '../components/session/SessionHistory'
import tulipLogo from '../assets/tulip.png'

interface Props {
  user: AppUser
  onSignOut: () => void
}

export default function UserHistory({ user, onSignOut }: Props) {
  const [sessions, setSessions] = useState<Session[]>([])

  useEffect(() => {
    api.getSessionHistory().then((d: any) => setSessions(d)).catch(() => {})
  }, [])

  return (
    <div className="min-h-screen bg-[#0f1012]">
      <header className="border-b border-white/5 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <img src={tulipLogo} alt="Tulip" className="w-6 h-6 object-contain" />
          <span style={{ fontFamily: 'Fredoka, sans-serif', fontWeight: 700, fontSize: 20 }} className="text-white">tulip</span>
        </div>
        <div className="flex items-center gap-4">
          {user.is_admin && (
            <Link to="/admin" className="text-xs text-brand hover:text-brand-dim transition-colors">Admin</Link>
          )}
          <span className="text-sm text-white/40">{user.display_name || user.wallet_address.slice(0, 8)}</span>
          <button className="btn-ghost text-sm" onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 py-8 space-y-6">
        <div className="flex gap-1 bg-white/5 p-1 rounded-xl">
          <Link to="/" className="flex-1 py-2 rounded-lg text-sm font-medium text-center text-white/40 hover:text-white/60 transition-colors">
            Session
          </Link>
          <Link to="/history" className="flex-1 py-2 rounded-lg text-sm font-medium text-center bg-white/10 text-white">
            History
          </Link>
        </div>

        <SessionHistory sessions={sessions} />
      </main>
    </div>
  )
}
