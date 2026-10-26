import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { MARK_COLORS } from './TelemetryChart.jsx'

const MAX_VISIBLE = 3
const DURATION_MS = 3000
// Matches the .toast-item transition in index.css. Removal runs on this timer,
// not on transitionend, so it still happens when reduced motion cuts the
// transition to ~0ms.
const EXIT_MS = 200

const TONES = {
  info: '#0284C7',
  success: '#16A34A',
  threshold: MARK_COLORS.threshold,
  model: MARK_COLORS.model,
  error: '#DC2626',
}

const ToastContext = createContext(() => {})
const ToastCompactContext = createContext(() => {})
const ToastDismissContext = createContext(() => {})

/**
 * const toast = useToast()
 * toast({ title, message?, compactText?, tone?: 'info'|'success'|'threshold'|'model'|'error', key?, onClick? })
 * message may be a string or JSX. With onClick the toast body is a button,
 * and clicking it runs onClick and dismisses the toast.
 * A toast with the same key as one still showing replaces it and restarts its
 * timer, so rapid repeats (e.g. flipping a filter) don't flood the stack.
 * compactText: the one line shown when toasts are minimized (defaults to title).
 * sticky: no timer and never pushed out by newer toasts; stays until its × or useToastDismiss.
 */
export function useToast() {
  return useContext(ToastContext)
}

/** const dismiss = useToastDismiss(); dismiss(key) -- closes the toast with that key, if showing */
export function useToastDismiss() {
  return useContext(ToastDismissContext)
}

/**
 * const setCompact = useToastCompact(); setCompact(true)
 * Minimized toasts: one small line each, bottom-right, so they stay out of the way of pages
 * and dialogs (the expanded chart keeps that corner free). false = full toasts, top-right.
 */
export function useToastCompact() {
  return useContext(ToastCompactContext)
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [compact, setCompact] = useState(false)
  const nextId = useRef(1)

  const remove = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  // Only marks the toast as leaving; ToastItem removes it once the exit transition has played
  const dismiss = useCallback((id) => {
    setToasts((list) => list.map((t) => (t.id === id ? { ...t, leaving: true } : t)))
  }, [])

  const push = useCallback(({ key, tone = 'info', ...content }) => {
    const id = nextId.current++
    setToasts((list) => {
      const same = key && list.find((t) => t.key === key && !t.leaving)
      if (same) {
        // Same toast again: move it to the top with the new content; the new version restarts its timer
        return [...list.filter((t) => t !== same), { ...same, ...content, tone, version: same.version + 1 }]
      }
      const next = [...list, { id, key, tone, ...content, version: 0, leaving: false }]
      // Over the limit: the oldest visible toasts start their exit (sticky ones don't count)
      const visible = next.filter((t) => !t.leaving && !t.sticky)
      const evicted = new Set(visible.slice(0, Math.max(0, visible.length - MAX_VISIBLE)).map((t) => t.id))
      return next.map((t) => (evicted.has(t.id) ? { ...t, leaving: true } : t))
    })
  }, [])

  const dismissKey = useCallback((key) => {
    setToasts((list) => list.map((t) => (t.key === key ? { ...t, leaving: true } : t)))
  }, [])

  return (
    <ToastContext.Provider value={push}>
      <ToastCompactContext.Provider value={setCompact}>
        <ToastDismissContext.Provider value={dismissKey}>
          {children}
          <ToastStack toasts={toasts} compact={compact} onDismiss={dismiss} onRemove={remove} />
        </ToastDismissContext.Provider>
      </ToastCompactContext.Provider>
    </ToastContext.Provider>
  )
}

function ToastStack({ toasts, compact, onDismiss, onRemove }) {
  // Full: newest on top, where the eye already is. Compact: the stack grows up from the
  // bottom corner, so the newest goes last (nearest the corner).
  const ordered = compact ? toasts : [...toasts].reverse()
  return (
    <div
      aria-live="polite" aria-label="Notifications" role="region"
      className={compact
        ? 'fixed bottom-3 right-3 z-[60] flex flex-col items-end pointer-events-none max-w-[calc(100vw-1.5rem)]'
        : 'fixed top-4 right-4 left-4 sm:left-auto sm:w-[360px] z-50 pointer-events-none'}
    >
      {ordered.map((t) => (
        <ToastItem key={t.id} toast={t} compact={compact} onDismiss={onDismiss} onRemove={onRemove} />
      ))}
    </div>
  )
}

function ToastBody({ title, message }) {
  return (
    <>
      <div className="text-sm font-semibold text-dark">{title}</div>
      {message && <div className="text-xs text-dark2 mt-0.5 break-words">{message}</div>}
    </>
  )
}

function ToastItem({ toast, compact, onDismiss, onRemove }) {
  const [paused, setPaused] = useState(false)
  const { id, title, message, compactText, tone, leaving, version, onClick, sticky } = toast

  // 3s countdown, restarted by a same-key update (version) and held while
  // hovered or focused so it can be read or dismissed without racing the timer
  useEffect(() => {
    if (paused || leaving || sticky) return undefined
    const t = setTimeout(() => onDismiss(id), DURATION_MS)
    return () => clearTimeout(t)
  }, [id, paused, leaving, version, onDismiss, sticky])

  // Leaving (dismissed, timed out or pushed past the limit): remove after the exit transition
  useEffect(() => {
    if (!leaving) return undefined
    const t = setTimeout(() => onRemove(id), EXIT_MS)
    return () => clearTimeout(t)
  }, [id, leaving, onRemove])

  const hover = {
    onMouseEnter: () => setPaused(true), onMouseLeave: () => setPaused(false),
    onFocus: () => setPaused(true), onBlur: () => setPaused(false),
  }

  if (compact) {
    const text = compactText || title
    return (
      <div className="toast-item" data-leaving={leaving || undefined}>
        <div className="min-h-0 overflow-hidden">
          <div className="pt-1.5">
            <div
              role={tone === 'threshold' || tone === 'error' ? 'alert' : 'status'}
              className="pointer-events-auto flex items-center gap-1.5 bg-white border border-border rounded-full shadow-md pl-2.5 pr-1 py-0.5 text-xs max-w-[320px]"
              title={typeof text === 'string' ? text : undefined}
              {...hover}
            >
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: TONES[tone] || TONES.info }} />
              {onClick ? (
                <button type="button" className="truncate text-dark hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-accentSky rounded"
                  onClick={() => { onClick(); onDismiss(id) }}>
                  {text}
                </button>
              ) : (
                <span className="truncate text-dark">{text}</span>
              )}
              <button
                type="button" aria-label="Dismiss notification" onClick={() => onDismiss(id)}
                className="shrink-0 w-5 h-5 rounded-full text-dark2 hover:bg-canvas2 leading-none"
              >
                ×
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="toast-item" data-leaving={leaving || undefined}>
      <div className="min-h-0 overflow-hidden">
        <div className="pb-2">
          <div
            role={tone === 'threshold' || tone === 'error' ? 'alert' : 'status'}
            className={`pointer-events-auto flex items-start gap-3 bg-white border border-border rounded-lg shadow-md px-3.5 py-3 ${
              onClick ? 'hover:shadow-lg hover:border-dark2/40 transition-shadow' : ''}`}
            style={{ borderLeft: `4px solid ${TONES[tone] || TONES.info}` }}
            {...hover}
          >
            {onClick ? (
              <button
                type="button" className="flex-1 min-w-0 text-left rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-accentSky"
                onClick={() => { onClick(); onDismiss(id) }}
              >
                <ToastBody title={title} message={message} />
              </button>
            ) : (
              <div className="flex-1 min-w-0"><ToastBody title={title} message={message} /></div>
            )}
            <button
              type="button" aria-label="Dismiss notification" onClick={() => onDismiss(id)}
              className="shrink-0 -mr-1 -mt-0.5 w-6 h-6 rounded text-dark2 hover:bg-canvas2 leading-none text-lg"
            >
              ×
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
