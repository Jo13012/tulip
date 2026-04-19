import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Copy, Check } from 'lucide-react'
import { useAppStore } from '../../store'
import type { ToastData } from '../../types'

const bar: Record<ToastData['type'], string> = {
  success: 'bg-emerald-400',
  error:   'bg-red-400',
  warning: 'bg-amber-400',
  info:    'bg-blue-400',
  loading: 'bg-brand',
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={copy}
      title="Copy error"
      className="flex-shrink-0 flex items-center justify-center w-5 h-5 rounded text-white/20 hover:text-white/55 transition-colors"
    >
      {copied ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} />}
    </button>
  )
}

function ToastItem({ toast }: { toast: ToastData }) {
  const removeToast = useAppStore((s) => s.removeToast)
  const fullText = [toast.title, toast.message, ...(toast.meta_lines ?? [])].filter(Boolean).join('\n')

  return (
    <motion.div
      initial={{ opacity: 0, y: 12, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.97 }}
      transition={{ duration: 0.15 }}
      className="flex items-stretch rounded-lg border border-white/[0.08] bg-[#18191d] shadow-2xl max-w-xs overflow-hidden"
    >
      {/* Color bar */}
      <div className={`w-[3px] flex-shrink-0 ${bar[toast.type]}`} />

      <div className="flex items-start gap-2 px-3.5 py-3 flex-1 min-w-0">
        <div className="flex-1 min-w-0">
          {toast.title && (
            <div className="text-[12px] font-semibold text-white/90 mb-0.5">{toast.title}</div>
          )}
          <div className="text-[11px] text-white/55 leading-relaxed break-words">{toast.message}</div>
          {toast.meta_lines?.length ? (
            <div className="mt-1.5 space-y-0.5">
              {toast.meta_lines.map((line, idx) => (
                <div key={`${toast.id}-${idx}`} className="text-[10px] text-white/30 break-words">{line}</div>
              ))}
            </div>
          ) : null}
          {toast.action_href && toast.action_label && (
            <a
              href={toast.action_href}
              target="_blank"
              rel="noreferrer"
              className="inline-flex mt-1.5 text-[10px] text-white/50 underline underline-offset-2 hover:text-white/80"
            >
              {toast.action_label}
            </a>
          )}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0 mt-0.5">
          {toast.type !== 'success' && (
            <CopyButton text={fullText} />
          )}         
          <button
            type="button"
            onClick={() => removeToast(toast.id)}
            className="text-[14px] leading-none text-white/20 hover:text-white/60 transition-colors"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      </div>
    </motion.div>
  )
}

export function ToastContainer() {
  const toasts = useAppStore((s) => s.toasts)
  return (
    <div className="fixed bottom-5 right-5 z-[9999] flex flex-col gap-1.5 items-end">
      <AnimatePresence>
        {toasts.map((t) => <ToastItem key={t.id} toast={t} />)}
      </AnimatePresence>
    </div>
  )
}
