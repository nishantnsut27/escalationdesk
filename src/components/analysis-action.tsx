import { lazy, Suspense, useCallback, useRef, type ReactNode } from 'react'
import { EffectBoundary, useContextFailure, useMedia, useVisibleEffect, useWebGL } from './effect-policy'

const SpiderAction = lazy(() => import('./spider-action'))
export type AnalysisActionProps = { children: ReactNode; disabled?: boolean; onClick: () => void; busy?: boolean }

/** Only explicit analysis actions opt in; ordinary buttons keep their shared implementation. */
export function AnalysisActionButton({ children, disabled = false, busy = false, onClick }: AnalysisActionProps) {
  const { ref, visible } = useVisibleEffect<HTMLSpanElement>()
  const motion = useMedia('(prefers-reduced-motion: no-preference) and (hover: hover) and (pointer: fine)')
  const webgl = useWebGL(), failed = useContextFailure(ref)
  const hadFocus = useRef(false)
  const restoreFocus = useCallback((button: HTMLButtonElement | null) => {
    if (button && hadFocus.current) button.focus({ preventScroll: true })
  }, [])
  const fallback = <button ref={restoreFocus} type="button" className="button primary analysis-native" disabled={disabled} aria-busy={busy || undefined} onClick={onClick}>{children}</button>
  return <span ref={ref} className="analysis-action" onFocusCapture={() => { hadFocus.current = true }} onBlurCapture={event => {
    hadFocus.current = !!event.relatedTarget && event.currentTarget.contains(event.relatedTarget as Node)
  }}>
    {visible && motion && webgl && !failed ? <EffectBoundary fallback={fallback}><Suspense fallback={fallback}>
      <SpiderAction disabled={disabled} busy={busy} onClick={onClick} onReady={restoreFocus}>{children}</SpiderAction>
    </Suspense></EffectBoundary> : fallback}
  </span>
}
