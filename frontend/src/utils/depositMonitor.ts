import type { DepositTracker } from '../types'

export const DEPOSIT_TRACKER_EVENT = 'polybot:deposit-tracker-updated'

function getDepositTrackerKey(userId: string) {
  return `deposit_tracker_${userId}`
}

export function loadDepositTracker(userId: string): DepositTracker | null {
  try {
    const raw = localStorage.getItem(getDepositTrackerKey(userId))
    if (!raw) return null
    return JSON.parse(raw) as DepositTracker
  } catch {
    return null
  }
}

export function saveDepositTracker(tracker: DepositTracker) {
  try {
    localStorage.setItem(getDepositTrackerKey(tracker.userId), JSON.stringify(tracker))
  } catch {}
  window.dispatchEvent(new Event(DEPOSIT_TRACKER_EVENT))
}

export function clearDepositTracker(userId: string) {
  try {
    localStorage.removeItem(getDepositTrackerKey(userId))
  } catch {}
  window.dispatchEvent(new Event(DEPOSIT_TRACKER_EVENT))
}
