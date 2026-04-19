import { motion, AnimatePresence } from 'framer-motion'

interface ConfirmModalProps {
  open: boolean
  title: string
  description: React.ReactNode
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'default' | 'danger'
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmModal({
  open, title, description,
  confirmLabel = 'Confirm', cancelLabel = 'Cancel',
  variant = 'default',
  onConfirm, onCancel,
}: ConfirmModalProps) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[9998] flex items-center justify-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} />
          <motion.div
            initial={{ scale: 0.92, opacity: 0, y: 8 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.92, opacity: 0, y: 8 }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            className="relative card w-full max-w-md mx-4"
          >
            <h2 className="text-lg font-semibold mb-2">{title}</h2>
            <div className="text-sm text-white/60 mb-6">{description}</div>
            <div className="flex gap-3 justify-end">
              <button className="btn-ghost" onClick={onCancel}>{cancelLabel}</button>
              <button
                className={variant === 'danger' ? 'btn-danger' : 'btn-primary'}
                onClick={onConfirm}
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
