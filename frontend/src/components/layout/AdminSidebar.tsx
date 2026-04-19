import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import tulipLogo from '../../assets/tulip.png'

const links = [
  { to: '/',                 label: 'User Panel', end: true },
  { to: '/admin',            label: 'Dashboard', end: true },
  { to: '/admin/strategies', label: 'Global Strategy' },
  { to: '/admin/sessions',   label: 'Sessions' },
  { to: '/admin/users',      label: 'Users' },
  { to: '/admin/audit',      label: 'Audit Log' },
]

interface Props { onSignOut: () => void }

export function AdminSidebar({ onSignOut }: Props) {
  const [open, setOpen] = useState(false)

  const nav = (
    <>
      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {links.map(({ to, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={() => setOpen(false)}
            className={({ isActive }) =>
              `block px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                isActive ? 'bg-brand/15 text-brand' : 'text-white/40 hover:text-white/70 hover:bg-white/5'
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-2 py-3 border-t border-white/[0.06]">
        <button
          className="w-full px-3 py-1.5 rounded-lg text-xs text-white/30 hover:text-white/60 hover:bg-white/5 text-left transition-colors"
          onClick={onSignOut}
        >
          Sign out
        </button>
      </div>
    </>
  )

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-44 flex-shrink-0 bg-[#0f1012] border-r border-white/[0.06] flex-col h-screen sticky top-0">
        <div className="px-4 py-3 border-b border-white/[0.06] flex items-center gap-2.5">
          <img src={tulipLogo} alt="Tulip" className="w-7 h-7 object-contain" />
          <div>
            <span style={{ fontFamily: 'Fredoka, sans-serif', fontWeight: 700, fontSize: 18 }} className="text-white leading-none">tulip</span>
            <div className="text-[10px] text-white/25">Admin</div>
          </div>
        </div>
        {nav}
      </aside>

      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between px-4 h-11 bg-[#0f1012] border-b border-white/[0.06]">
        <div className="flex items-center gap-2">
          <img src={tulipLogo} alt="Tulip" className="w-6 h-6 object-contain" />
          <span style={{ fontFamily: 'Fredoka, sans-serif', fontWeight: 700, fontSize: 17 }} className="text-white">tulip</span>
          <span className="text-white/25 font-normal text-xs">Admin</span>
        </div>
        <button
          className="p-1.5 rounded-lg text-white/40 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
          onClick={() => setOpen(v => !v)}
          aria-label="Menu"
        >
          {open ? (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M2 2l12 12M14 2L2 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          )}
        </button>
      </div>

      {/* Mobile drawer */}
      {open && (
        <div className="md:hidden fixed inset-0 z-30" onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <aside
            className="absolute top-11 left-0 bottom-0 w-56 bg-[#0f1012] border-r border-white/[0.06] flex flex-col"
            onClick={e => e.stopPropagation()}
          >
            {nav}
          </aside>
        </div>
      )}
    </>
  )
}
