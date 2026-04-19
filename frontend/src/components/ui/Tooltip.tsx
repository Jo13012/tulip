import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Info } from 'lucide-react'

interface TooltipProps {
  content: React.ReactNode
  title?: string
  children: React.ReactNode
  position?: 'top' | 'bottom' | 'left' | 'right'
  maxWidth?: number
}

export function Tooltip({ content, title, children, position = 'top', maxWidth = 200 }: TooltipProps) {
  const [visible, setVisible] = useState(false)
  const [coords, setCoords] = useState({ top: 0, left: 0 })
  const triggerRef = useRef<HTMLSpanElement>(null)
  const hideTimer = useRef<ReturnType<typeof setTimeout>>()

  const show = () => {
    clearTimeout(hideTimer.current)
    if (triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect()
      const GAP = 8
      let top = 0, left = 0
      if (position === 'top') {
        top  = r.top + window.scrollY - GAP
        left = r.left + window.scrollX + r.width / 2
      } else if (position === 'bottom') {
        top  = r.bottom + window.scrollY + GAP
        left = r.left + window.scrollX + r.width / 2
      } else if (position === 'left') {
        top  = r.top + window.scrollY + r.height / 2
        left = r.left + window.scrollX - GAP
      } else {
        top  = r.top + window.scrollY + r.height / 2
        left = r.right + window.scrollX + GAP
      }
      setCoords({ top, left })
    }
    setVisible(true)
  }
  const hide = () => { hideTimer.current = setTimeout(() => setVisible(false), 80) }
  useEffect(() => () => clearTimeout(hideTimer.current), [])

  // Transform so bubble is correctly anchored
  const bubbleTransform =
    position === 'top'    ? 'translate(-50%, -100%)' :
    position === 'bottom' ? 'translate(-50%, 0)'     :
    position === 'left'   ? 'translate(-100%, -50%)' :
                            'translate(0, -50%)'

  const motionProps = {
    top:    { initial: { opacity: 0, y: 4 },  animate: { opacity: 1, y: 0 } },
    bottom: { initial: { opacity: 0, y: -4 }, animate: { opacity: 1, y: 0 } },
    left:   { initial: { opacity: 0, x: 4 },  animate: { opacity: 1, x: 0 } },
    right:  { initial: { opacity: 0, x: -4 }, animate: { opacity: 1, x: 0 } },
  }[position]

  return (
    <span
      ref={triggerRef}
      className="relative inline-flex items-center"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={e => { e.stopPropagation(); visible ? hide() : show() }}
    >
      {children}
      {createPortal(
        <AnimatePresence>
          {visible && (
            <motion.div
              {...motionProps}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.12, ease: 'easeOut' }}
              style={{
                position: 'fixed',
                top: coords.top,
                left: coords.left,
                transform: bubbleTransform,
                width: maxWidth,
                zIndex: 99999,
                pointerEvents: 'none',
              }}
            >
              <div className="bg-[#1e1f23] border border-white/[0.10] rounded-lg shadow-[0_8px_24px_rgba(0,0,0,0.65)] px-3 py-2">
                {title && (
                  <div className="text-[10px] font-semibold text-white/75 mb-0.5 leading-tight">{title}</div>
                )}
                <div className="text-[10px] text-white/40 leading-snug">{content}</div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body
      )}
    </span>
  )
}

export function InfoTip({ content, title }: { content: React.ReactNode; title?: string }) {
  return (
    <Tooltip content={content} title={title}>
      <span className="inline-flex items-center justify-center text-white/25 hover:text-white/50 transition-colors duration-150 ml-1 shrink-0 cursor-default">
        <Info size={13} strokeWidth={1.75} />
      </span>
    </Tooltip>
  )
}
