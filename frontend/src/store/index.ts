/**
 * store/index.ts — Single Zustand store for the unified app.
 *
 * Auth state comes from Privy (ready/authenticated) + backend /auth/me (user profile).
 * No JWT stored here. No token stored here.
 * The Privy access token is fetched on demand via getAccessToken() before each API call.
 */
import { create } from 'zustand'
import type { AppUser, Session, Strategy, ToastData, LogEntry, RuntimeSlotStats } from '../types'

interface AppState {
  // User profile (from backend /auth/me after Privy login)
  user: AppUser | null
  setUser: (u: AppUser | null) => void

  // Strategy
  strategy: Strategy | null
  setStrategy: (s: Strategy | null) => void

  // Active session
  activeSession: Session | null
  setActiveSession: (
    s: Session | null | ((prev: Session | null) => Session | null)
  ) => void

  // Live slot stats (updated from WS)
  slotStats: RuntimeSlotStats[]
  setSlotStats: (s: RuntimeSlotStats[]) => void

  // Live logs
  logs: LogEntry[]
  addLog: (log: LogEntry) => void
  clearLogs: () => void

  // Toasts
  toasts: ToastData[]
  addToast: (toast: Omit<ToastData, 'id'>, id?: string) => string
  upsertToast: (id: string, toast: Omit<ToastData, 'id'>) => string
  removeToast: (id: string) => void
  clearToasts: () => void

  // Funding status (shared across UI)
  fundingStatus: any | null
  setFundingStatus: (status: any | null) => void
}

export const useAppStore = create<AppState>((set, get) => ({
  user: null,
  setUser: (u) => set({ user: u }),

  strategy: null,
  setStrategy: (s) => set({ strategy: s }),

  activeSession: null,
  setActiveSession: (s) =>
    set((state) => ({
      activeSession: typeof s === 'function' ? s(state.activeSession) : s,
    })),

  slotStats: [],
  setSlotStats: (s) => set({ slotStats: s }),

  logs: [],
  addLog: (log) => set((state) => ({
    logs: [...state.logs.slice(-499), log],
  })),
  clearLogs: () => set({ logs: [] }),

  toasts: [],
  addToast: (toast, providedId) => {
    const id = providedId ?? Math.random().toString(36).slice(2)
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }))
    const duration = toast.duration ?? (toast.type === 'loading' ? 0 : 4000)
    if (duration > 0) {
      setTimeout(() => get().removeToast(id), duration)
    }
    return id
  },
  upsertToast: (id, toast) => {
    set((state) => {
      const nextToast = { ...toast, id }
      const existing = state.toasts.find((t) => t.id === id)
      return {
        toasts: existing
          ? state.toasts.map((t) => (t.id === id ? nextToast : t))
          : [...state.toasts, nextToast],
      }
    })
    const duration = toast.duration ?? (toast.type === 'loading' ? 0 : 4000)
    if (duration > 0) {
      setTimeout(() => get().removeToast(id), duration)
    }
    return id
  },
  removeToast: (id) => set((state) => ({
    toasts: state.toasts.filter((t) => t.id !== id),
  })),
  clearToasts: () => set({ toasts: [] }),

  fundingStatus: null,
  setFundingStatus: (status) => set({ fundingStatus: status }),
}))
