import { lazy, memo, Suspense, useRef } from 'react'
import { EffectBoundary, useContextFailure, useMedia, usePalette, useVisibleEffect, useWebGL } from './effect-policy'
import { retainArtworkFrame, useArtworkPaint } from './artwork-frame'

const VectorWordmark = lazy(() => import('./originkit/ui/vector-wordmark'))
const MoonlitPetals = lazy(() => import('./originkit/ui/moonlit-petals'))
const fitted = { minWidth: 0, minHeight: 0, width: '100%', height: '100%' }

export function FooterWordmark() {
  const { ref, visible } = useVisibleEffect<HTMLDivElement>()
  const motion = useMedia('(prefers-reduced-motion: no-preference) and (hover: hover) and (pointer: fine)')
  const palette = usePalette(), webgl = useWebGL(), failed = useContextFailure(ref)
  const active = visible && motion && webgl && !failed && palette
  return <div ref={ref} className="home-wordmark effect-wordmark" aria-hidden="true" onPointerLeave={event => {
    // The original listens only for moves. Explicitly park its pointer outside
    // the canvas on leave so the reveal cannot stay stuck above the next target.
    const renderer = event.currentTarget.querySelector<HTMLElement>('.wordmark-live > div')
    if (renderer) {
      const box = renderer.getBoundingClientRect()
      renderer.dispatchEvent(new PointerEvent('pointermove', { clientX: box.left - box.width, clientY: box.top + box.height / 2 }))
    }
  }}>
    <span className="wordmark-static">TraceDesk</span>
    {active && <div className="wordmark-live"><EffectBoundary><Suspense fallback={null}>
      <VectorWordmark text="TraceDesk" background={palette.background} textColor={palette.foreground} shade={palette.foreground} accent="rgba(0, 0, 0, 0)"
        font={{ fontFamily: 'Geist', fontWeight: 550, fontSize: 236, letterSpacing: '-0.045em' }}
        handles={{ size: 0, labels: false, spread: 27 }} reach={170}
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
