const BASE = import.meta.env.VITE_API_URL

let _getToken: () => Promise<string | null> = async () => null

export function initApiAuth(getPrivyToken: () => Promise<string | null>) {
  _getToken = getPrivyToken
}

export async function getAuthToken(): Promise<string | null> {
  return _getToken()
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = await Promise.race([
    _getToken(),
    new Promise<string | null>((_, reject) => {
      setTimeout(() => reject(new Error('Auth token fetch timed out')), 8000)
    }),
  ])

  // Keep a fresh snapshot for the WS hook (can't do async in WS constructor)
  if (token) {
    try { sessionStorage.setItem('privy_token_snapshot', token) } catch {}
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string> || {}),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  let res = await fetch(`${BASE}${path}`, { ...opts, headers })


  if (res.status === 401) {
    // Retry once with a fresh token (Privy occasionally expires mid-session).
    const retryToken = await _getToken()
    if (retryToken && retryToken !== token) {
      const retryHeaders: Record<string, string> = {
        'Content-Type': 'application/json',
        ...(opts.headers as Record<string, string> || {}),
        'Authorization': `Bearer ${retryToken}`,
      }
      res = await fetch(`${BASE}${path}`, { ...opts, headers: retryHeaders })
      if (res.status !== 401) {
        return res.json()
      }
    }
    throw new Error('UNAUTHORIZED')
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    const e = new Error(err.detail || 'Request failed') as any
    e.status = res.status
    throw e
  }
  return res.json()
}

export const api = {
  syncUser: () =>
    request('/api/v1/auth/sync', { method: 'POST', body: JSON.stringify({}) }),

  getMe: () => request('/api/v1/auth/me'),
  updateProfile: (displayName: string) =>
    request('/api/v1/auth/profile', {
      method: 'PATCH',
      body: JSON.stringify({ display_name: displayName }),
    }),

  getStrategy: () => request('/api/v1/user/strategy/current'),

  checkViability: (grossCapital: number) =>
    request('/api/v1/user/sessions/viability', { method: 'POST', body: JSON.stringify({ gross_capital: grossCapital }) }),

  launchSession: (data: object) =>
    request('/api/v1/user/sessions/launch', { method: 'POST', body: JSON.stringify(data) }),

  getActiveSession: () => request('/api/v1/user/sessions/active'),

  stopSession: (sessionId: string, mode: 'soft' | 'hard') =>
    request(`/api/v1/user/sessions/${sessionId}/stop`, {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),

  getSessionHistory: () => request('/api/v1/user/sessions/history'),

  getProfile: () => request('/api/v1/user/profile/'),
  getReferrals: () => request<import('../types').ReferralListItem[]>('/api/v1/user/profile/referrals'),
  lookupReferrer: (username: string) =>
    request(`/api/v1/user/profile/referrer/lookup?username=${encodeURIComponent(username)}`),
  assignReferrer: (username: string) =>
    request('/api/v1/user/profile/referrer', {
      method: 'POST',
      body: JSON.stringify({ username }),
    }),

  listWallets: () => request('/api/v1/user/wallets/'),
  getOnboardingStatus: () => request('/api/v1/user/wallets/onboarding-status'),
  getFundingStatus: () => request('/api/v1/user/wallets/funding-status'),
  forceFunding: () => request('/api/v1/user/wallets/funding-force', { method: 'POST' }),
  getBridgeHistory: () => request<import('../components/user/BridgeHistoryModal').BridgeTx[]>('/api/v1/user/wallets/bridge-history'),
  getUserDailyVolume: (days = 60) => request(`/api/v1/user/analytics/daily-volume?days=${days}`),
  getUserDailyCpm: (days = 60) => request(`/api/v1/user/analytics/daily-cpm?days=${days}`),
  getUserDailyMultiple: (days = 60) => request(`/api/v1/user/analytics/daily-multiple?days=${days}`),

  admin: {
    // ── Global strategy (single global strategy model) ──────────────────────
    getGlobalStrategy:      () => request('/api/v1/admin/strategy/global'),
    getSlotVolumeStats:     () => request('/api/v1/admin/strategy/global/slot-volume'),
    getPublishedSlots:      () => request('/api/v1/admin/strategy/global-published-slots'),
    updateStrategy:     (data: object) => request('/api/v1/admin/strategy/global', { method: 'PATCH', body: JSON.stringify(data) }),
    publishVersion:     (data: object) => request('/api/v1/admin/strategy/global/publish', { method: 'POST', body: JSON.stringify(data) }),
    createSlot:         (data: object) => request('/api/v1/admin/strategy/global/slots', { method: 'POST', body: JSON.stringify(data) }),
    setSlotMarket:      (slotId: string, data: object) => request(`/api/v1/admin/strategy/slots/${slotId}/market`, { method: 'POST', body: JSON.stringify(data) }),
    patchSlot:          (slotId: string, data: { allocation_pct?: number; enabled?: boolean }) => request(`/api/v1/admin/strategy/slots/${slotId}`, { method: 'PATCH', body: JSON.stringify(data) }),
    deleteSlot:         (slotId: string) => request(`/api/v1/admin/strategy/slots/${slotId}`, { method: 'DELETE' }),
    restoreSlot:        (slotId: string) => request(`/api/v1/admin/strategy/slots/${slotId}/restore`, { method: 'POST' }),
    revertSlot:         (slotId: string) => request(`/api/v1/admin/strategy/slots/${slotId}/revert`, { method: 'POST' }),
    queueReplacement:   (ssId: string, data: object) => request(`/api/v1/admin/strategy/session-slots/${ssId}/replace`, { method: 'POST', body: JSON.stringify(data) }),
    initStrategy:       (data?: object) => request('/api/v1/admin/strategy/init', { method: 'POST', body: JSON.stringify(data ?? {}) }),

    // ── Sessions ─────────────────────────────────────────────────────────────
    listSessions:   (limit = 50) => request(`/api/v1/admin/sessions/?limit=${limit}`),
    getSession:     (id: string) => request(`/api/v1/admin/sessions/${id}`),
    stopSession:    (id: string, mode: 'soft' | 'hard') => request(`/api/v1/admin/sessions/${id}/stop`, { method: 'POST', body: JSON.stringify({ mode }) }),
    forceStop:      (id: string) => request(`/api/v1/admin/sessions/${id}/force-stop`, { method: 'POST' }),
    retryRecovery:      (id: string) => request(`/api/v1/admin/sessions/${id}/retry-recovery`, { method: 'POST' }),
    forceReplaceSlot:   (sessionId: string, slotId: string) => request(`/api/v1/admin/sessions/${sessionId}/slots/${slotId}/force-replace`, { method: 'POST' }),

    // ── Analytics ────────────────────────────────────────────────────────────
    globalMetrics:           () => request('/api/v1/admin/analytics/global'),
    globalStrategyAnalytics: () => request('/api/v1/admin/analytics/global-strategy'),
    slotAnalytics:           (id: string) => request(`/api/v1/admin/analytics/slot/${id}`),
    marketAnalytics:         (slotId?: string) => request(`/api/v1/admin/analytics/markets${slotId ? `?strategy_slot_id=${slotId}` : ''}`),
    dailyVolume:             (days = 60) => request(`/api/v1/admin/analytics/daily-volume?days=${days}`),
    dailyCpm:                (days = 60) => request(`/api/v1/admin/analytics/daily-cpm?days=${days}`),
    dailyMultiple:           (days = 60) => request(`/api/v1/admin/analytics/daily-multiple?days=${days}`),
    referralLeaderboard:     () => request('/api/v1/admin/analytics/referral-leaderboard'),

    // ── Market resolution ────────────────────────────────────────────────────
    resolveEvent: (slug: string) => request(`/api/v1/admin/strategy/resolve-event?slug=${encodeURIComponent(slug)}`),
    getBook:      (tokenId: string) => request(`/api/v1/admin/strategy/book/${tokenId}`),

    // ── Controls ─────────────────────────────────────────────────────────────
    setGlobalPause: (strategyId: string, paused: boolean) =>
      request('/api/v1/admin/controls/global-pause', { method: 'POST', body: JSON.stringify({ strategy_id: strategyId, paused }) }),
    getAuditLog: (entityType?: string, entityId?: string) =>
      request(`/api/v1/admin/controls/audit?limit=200${entityType ? `&entity_type=${entityType}` : ''}${entityId ? `&entity_id=${entityId}` : ''}`),

    // ── Users ─────────────────────────────────────────────────────────────────
    listUsers: (limit = 100) => request(`/api/v1/admin/users/?limit=${limit}`),
    // fee_override_bps: number to set override, -1 to clear override
    updateUser: (id: string, patch: { fee_override_bps?: number; display_name?: string; free_fee_days?: number }) =>
      request(`/api/v1/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    stopUserSession: (userId: string, mode: 'soft' | 'hard') =>
      request(`/api/v1/admin/users/${userId}/stop-session`, { method: 'POST', body: JSON.stringify({ mode }) }),
    launchForUser: (data: {
      user_id: string; wallet_id: string; gross_capital: number;
      stop_mode: string; stop_days?: number | null; target_volume?: number | null; session_sl_pct: number
    }) => request('/api/v1/admin/sessions/launch-for-user', { method: 'POST', body: JSON.stringify(data) }),
    getUserPolyState: (userId: string) =>
      request(`/api/v1/admin/users/${userId}/poly-state`),
    nuclearClose: (userId: string, body: { cancel_orders: boolean; cancel_order_ids?: string[]; sell_token_ids: string[] }) =>
      request(`/api/v1/admin/users/${userId}/nuclear-close`, { method: 'POST', body: JSON.stringify(body) }),
  },
}
