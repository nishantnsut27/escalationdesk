import { createContext, isValidElement, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { TriangleAlert, X } from 'lucide-react'

type Toast = { id: string; message: string; content: ReactNode; dismissed: boolean; returnFocus: HTMLElement | null }
type ToastActions = { show: (id: string, message: string, content: ReactNode) => void; remove: (id: string) => void }
const ToastContext = createContext<ToastActions | null>(null)

function messageText(value: ReactNode): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(messageText).join(' ')
  if (isValidElement<{ children?: ReactNode }>(value)) return messageText(value.props.children)
  return ''
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const lastFocus = useRef<HTMLElement | null>(null)
  useEffect(() => {
    const rememberFocus = (event: FocusEvent) => {
      if (event.target instanceof HTMLElement && event.target !== document.body && !event.target.closest('.toast')) lastFocus.current = event.target
    }
    document.addEventListener('focusin', rememberFocus)
    return () => document.removeEventListener('focusin', rememberFocus)
  }, [])
  const show = useCallback((id: string, message: string, content: ReactNode) => {
    const returnFocus = lastFocus.current
    setToasts(current => {
      const existing = current.find(item => item.id === id)
      const toast = { id, message, content, dismissed: existing?.message === message ? existing.dismissed : false, returnFocus: existing?.returnFocus || returnFocus }
      return existing ? current.map(item => item.id === id ? toast : item) : [...current, toast]
    })
  }, [])
  const remove = useCallback((id: string) => setToasts(current => current.filter(item => item.id !== id)), [])
  const actions = useMemo(() => ({ show, remove }), [show, remove])
  const dismiss = (message: string) => {
    const target = toasts.find(item => item.message === message)?.returnFocus
    if (document.activeElement?.closest('.toast') && target?.isConnected) target.focus({ preventScroll: true })
    setToasts(current => current.map(item => item.message === message ? { ...item, dismissed: true } : item))
  }
  const visible = [...new Map(toasts.filter(item => !item.dismissed).map(item => [item.message, item])).values()].slice(-3)
  return <ToastContext.Provider value={actions}>{children}<section className="toast-viewport" aria-label="Notifications">
    {visible.map(toast => <div className="toast toast-error" key={toast.id} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); dismiss(toast.message) } }}>
      <span className="toast-icon" aria-hidden="true"><TriangleAlert size={19} /></span>
      <div className="toast-content" role="alert" aria-atomic="true"><strong>Something went wrong</strong><div>{toast.content}</div></div>
      <button type="button" className="toast-dismiss" aria-label="Dismiss notification" onClick={() => dismiss(toast.message)}><X size={17} aria-hidden="true" /></button>
    </div>)}
  </section></ToastContext.Provider>
}

// One mounted error owns one toast; polling updates it without reopening a dismissal.
export function ErrorToast({ children }: { children: ReactNode }) {
  const actions = useContext(ToastContext), id = useId(), message = messageText(children)
  useEffect(() => { actions?.show(id, message, children) }, [actions, id, message, children])
  useEffect(() => () => { actions?.remove(id) }, [actions, id])
  return actions ? null : <div className="notice notice-error" role="alert">{children}</div>
}
