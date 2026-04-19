import { useEffect, useRef, useCallback } from 'react'
import { useAppStore } from '../store'
import { getAuthToken } from '../utils/api'

const WS_BASE = import.meta.env.VITE_WS_URL || 'ws://localhost:8010'

// Close codes that mean "don't reconnect"
const TERMINAL_CODES = new Set([4001, 4004])

export function useSessionWebSocket(sessionId: string | null) {
  const wsRef    = useRef<WebSocket | null>(null)
  const deadRef  = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const setSession   = useAppStore((s) => s.setActiveSession)
  const setSlotStats = useAppStore((s) => s.setSlotStats)
  const addLog       = useAppStore((s) => s.addLog)
  const addToast     = useAppStore((s) => s.addToast)

  const connect = useCallback(async () => {
    if (!sessionId || deadRef.current) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const token = await getAuthToken()
    if (!token || deadRef.current) return

    const ws = new WebSocket(`${WS_BASE}/ws/user/${sessionId}?token=${encodeURIComponent(token)}`)

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'summary') {
          const rt = msg.data
          if (rt?.status && ['stopped', 'error'].includes(rt.status)) {
            deadRef.current = true
          }
          setSession((prev: any) => prev ? { ...prev, runtime: rt } : null)
          if (Array.isArray(msg.slots)) setSlotStats(msg.slots)
        } else if (msg.type === 'logs') {
          msg.data.forEach((log: any) => addLog(log))
        } else if (msg.type === 'slot_rotation') {
          addToast({ type: 'info', message: 'Strategy updated — slot configuration changed.' })
        } else if (msg.type === 'error') {
          addToast({ type: 'error', message: msg.detail })
          deadRef.current = true
          // Mark session as stopped so ActiveSession calls onStopped()
          setSession((prev: any) => prev
            ? { ...prev, runtime: { ...(prev.runtime ?? {}), status: 'stopped', health_state: 'stopped' } }
            : null
          )
        }
      } catch {}
    }

    ws.onclose = (ev) => {
      wsRef.current = null
      // Terminal close codes or already dead → no reconnect
      if (deadRef.current || TERMINAL_CODES.has(ev.code)) {
        deadRef.current = true
        return
      }
      timerRef.current = setTimeout(() => connect(), 3000)
    }

    ws.onerror = () => ws.close()
    wsRef.current = ws
  }, [sessionId])

  useEffect(() => {
    deadRef.current = false
    connect()
    return () => {
      deadRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [connect])
}
