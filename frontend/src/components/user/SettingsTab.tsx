import { useEffect, useState } from 'react'
import { usePrivy } from '@privy-io/react-auth'
import { KeyRound } from 'lucide-react'
import type { AppUser } from '../../types'
import { useAppStore } from '../../store'
import { api } from '../../utils/api'
import { Section, Row, AddressField, InlineInput, GhostButton } from '../ui/Field'

interface Props {
  user: AppUser
  onSignOut: () => void
}

export function SettingsTab({ user }: Props) {
  const addToast = useAppStore(s => s.addToast)
  const setUser = useAppStore(s => s.setUser)
  const { exportWallet } = usePrivy()

  const [exporting, setExporting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [displayName, setDisplayName] = useState(user.display_name || '')

  useEffect(() => { setDisplayName(user.display_name || '') }, [user.display_name])

  async function handleExport() {
    if (!user.wallet_address) return
    setExporting(true)
    try { await exportWallet({ address: user.wallet_address }) }
    catch (e: any) { addToast({ type: 'error', message: e?.message ?? 'Export failed' }) }
    finally { setExporting(false) }
  }

  async function handleSaveDisplayName() {
    const value = displayName.trim()
    if (!/^[A-Za-z0-9]{3,15}$/.test(value)) {
      addToast({ type: 'error', message: 'Username must be 3-15 letters or numbers' })
      return
    }
    setSaving(true)
    try {
      const profile: any = await api.updateProfile(value)
      setUser(profile)
      setDisplayName(profile.display_name || value)
      addToast({ type: 'success', message: 'Username updated' })
    } catch (e: any) {
      addToast({ type: 'error', message: e.message })
    } finally {
      setSaving(false)
    }
  }

  const addr = user.wallet_address || ''
  const canChangeUsername = user.can_change_username ?? true

  return (
    <div className="p-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
        <Section title="Account">
          <Row label="Username">
            <InlineInput
              type="text"
              minLength={3}
              maxLength={15}
              value={displayName}
              onChange={e => setDisplayName(e.target.value.replace(/[^A-Za-z0-9]/g, ''))}
              placeholder="Enter username"
              suffix={(
                <button
                  disabled={saving || !canChangeUsername || displayName.trim() === (user.display_name || '')}
                  onClick={handleSaveDisplayName}
                  className="px-3 py-1.5 text-[11px] font-medium text-white/40 hover:text-white/70 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  {saving ? 'Saving...' : 'Save'}
                </button>
              )}
            />
          </Row>

          <Row label="Wallet">
            {addr
              ? <AddressField value={addr} />
              : <span className="text-[11px] text-white/20 font-mono">-</span>
            }
          </Row>

          <Row label="Private key">
            <GhostButton disabled={exporting || !addr} onClick={handleExport}>
              <KeyRound size={11} />
              {exporting ? 'Opening...' : 'Export wallet'}
            </GhostButton>
          </Row>
        </Section>
      </div>
    </div>
  )
}
