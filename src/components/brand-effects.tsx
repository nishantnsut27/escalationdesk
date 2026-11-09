import { lazy, memo, Suspense, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { EffectBoundary, useContextFailure, useMedia, usePalette, useVisibleEffect, useWebGL } from './effect-policy'
import { retainArtworkFrame, useArtworkPaint } from './artwork-frame'

const VectorWordmark = lazy(() => import('./originkit/ui/vector-wordmark'))
const MoonlitPetals = lazy(() => import('./originkit/ui/moonlit-petals'))
const fitted = { minWidth: 0, minHeight: 0, width: '100%', height: '100%' }

export function FooterWordmark() {
  const { ref, visible } = useVisibleEffect<HTMLDivElement>()
  const pointer = useRef<{ clientX: number; clientY: number } | null>(null)
  const [damping, setDamping] = useState(60)
  const settle = useRef(0)
  const motion = useMedia('(prefers-reduced-motion: no-preference) and (hover: hover) and (pointer: fine)')
  const palette = usePalette(), webgl = useWebGL(), failed = useContextFailure(ref)
  const active = visible && motion && webgl && !failed && palette
  const forward = (host: HTMLElement, point: { clientX: number; clientY: number }) => {
    const renderer = host.querySelector<HTMLElement>('.wordmark-live > div')
    renderer?.dispatchEvent(new PointerEvent('pointermove', { clientX: point.clientX, clientY: point.clientY, pointerType: 'mouse' }))
  }
  const enter = (host: HTMLElement, point: { clientX: number; clientY: number }) => {
    // Snap the first rendered frame to the entry position, then retain the
    // original damping for continuous tracking and nearest-side exit.
    cancelAnimationFrame(settle.current)
    setDamping(10000)
    forward(host, point)
    settle.current = requestAnimationFrame(() => {
      if (pointer.current) forward(host, pointer.current)
      settle.current = requestAnimationFrame(() => setDamping(60))
    })
  }
  useEffect(() => {
    const host = ref.current
    if (!active || !host) return
    const sync = () => { if (pointer.current) enter(host, pointer.current) }
    const resync = () => { if (pointer.current) forward(host, pointer.current) }
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(host, { childList: true, subtree: true })
    window.addEventListener('resize', resync)
    window.addEventListener('scroll', resync, { passive: true, capture: true })
    window.visualViewport?.addEventListener('resize', resync)
    return () => {
      observer.disconnect(); cancelAnimationFrame(settle.current)
      window.removeEventListener('resize', resync)
      window.removeEventListener('scroll', resync, true)
      window.visualViewport?.removeEventListener('resize', resync)
    }
  }, [active, ref])
  const track = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!event.nativeEvent.isTrusted) return
    const entering = !pointer.current
    pointer.current = { clientX: event.clientX, clientY: event.clientY }
    if (entering) enter(event.currentTarget, pointer.current)
    else forward(event.currentTarget, pointer.current)
  }
  return <div ref={ref} className="home-wordmark effect-wordmark" aria-hidden="true" onPointerEnter={track} onPointerMove={track} onPointerLeave={event => {
    pointer.current = null
    cancelAnimationFrame(settle.current); setDamping(60)
    const renderer = event.currentTarget.querySelector<HTMLElement>('.wordmark-live > div')
    if (!renderer) return
    const box = renderer.getBoundingClientRect()
    const exits = [
      { distance: Math.abs(event.clientX - box.left), clientX: box.left - box.width, clientY: event.clientY },
      { distance: Math.abs(event.clientX - box.right), clientX: box.right + box.width, clientY: event.clientY },
      { distance: Math.abs(event.clientY - box.top), clientX: event.clientX, clientY: box.top - box.height },
      { distance: Math.abs(event.clientY - box.bottom), clientX: event.clientX, clientY: box.bottom + box.height },
    ]
    const exit = exits.sort((left, right) => left.distance - right.distance)[0]
    forward(event.currentTarget, exit)
  }}>
    <span className="wordmark-static">TraceDesk</span>
    {active && <div className="wordmark-live"><EffectBoundary><Suspense fallback={null}>
      <VectorWordmark text="TraceDesk" background={palette.background} textColor={palette.foreground} shade={palette.foreground} accent="rgba(0, 0, 0, 0)"
        font={{ fontFamily: 'Geist', fontWeight: 550, fontSize: 236, letterSpacing: '-0.045em' }}
        handles={{ size: 0, labels: false, spread: 27 }} reach={170} speed={0} damping={damping}
        style={fitted} />
    </Suspense></EffectBoundary></div>}
  </div>
}

export const AuthArtwork = memo(function AuthArtwork() {
  const live = useRef<HTMLDivElement>(null)
  const retained = useRef<HTMLCanvasElement>(null)
  const { ref, visible } = useVisibleEffect<HTMLDivElement>(() => retainArtworkFrame(live.current, retained.current))
  const desktop = useMedia('(min-width: 761px)')
  const motion = useMedia('(prefers-reduced-motion: no-preference)')
  const active = desktop && visible && motion
  useArtworkPaint(live, retained, active)
  return <div ref={ref} className="auth-artwork" aria-hidden="true">
    {desktop && <>
      <img className="auth-artwork-poster" src="/originkit/moonlit-petals-poster.jpg" alt="" decoding="async" />
      <canvas ref={retained} className="auth-artwork-retained" />
      <div ref={live} className="auth-artwork-live">{active && <EffectBoundary><Suspense fallback={null}>
        <MoonlitPetals style={fitted} maxDpr={1} hover={0} />
      </Suspense></EffectBoundary>}</div>
    </>}
  </div>
})
