import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import SpiderButton from './originkit/ui/spider-button'
import { usePalette } from './effect-policy'
import type { AnalysisActionProps } from './analysis-action'

export default function SpiderAction({ children, disabled = false, busy = false, onClick, onReady }: AnalysisActionProps & { onReady: (button: HTMLButtonElement) => void }) {
  const root = useRef<HTMLSpanElement>(null), stage = useRef<HTMLDivElement>(null)
  const [button, setButton] = useState<HTMLButtonElement | null>(null)
  const [hovered, setHovered] = useState(false), [pressed, setPressed] = useState(false)
  const [padding, setPadding] = useState(0)
  const palette = usePalette()

  useLayoutEffect(() => {
    const native = stage.current?.querySelector<HTMLButtonElement>('.spider-pill')
    if (!native) return
    // The original exposes onClick, but not disabled/children. Bridge its existing
    // native button and portal the label into it; never nest or duplicate buttons.
    native.disabled = disabled
    if (busy) native.setAttribute('aria-busy', 'true')
    else native.removeAttribute('aria-busy')
    setButton(native)
    onReady(native)
  }, [disabled, busy, palette, onReady])

  useLayoutEffect(() => {
    const element = root.current
    if (!element) return
    const measure = () => {
      const box = element.getBoundingClientRect()
      setPadding(Math.max(0, Math.min(96, box.left - 8, document.documentElement.clientWidth - box.right - 8)))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    window.addEventListener('resize', measure)
    return () => { observer.disconnect(); window.removeEventListener('resize', measure) }
  }, [])

  useEffect(() => {
    const renderer = stage.current?.firstElementChild
    if (!(renderer instanceof HTMLElement) || disabled) return
    let inside = false
    let last: { clientX: number; clientY: number; pointerType: string } | null = null
    const dispatch = (type: 'pointermove' | 'pointerleave', point: { clientX: number; clientY: number; pointerType: string }) => {
      const box = renderer.getBoundingClientRect()
      const localX = box.width ? (point.clientX - box.left) * renderer.offsetWidth / box.width : 0
      const localY = box.height ? (point.clientY - box.top) * renderer.offsetHeight / box.height : 0
      const forwarded = new PointerEvent(type, { clientX: point.clientX, clientY: point.clientY, pointerType: point.pointerType, bubbles: true })
      Object.defineProperties(forwarded, { offsetX: { value: localX }, offsetY: { value: localY } })
      renderer.dispatchEvent(forwarded)
    }
    // Extra drawing space must not intercept adjacent links. Forward only pointer
    // movement to the original renderer; clicks stay on the real native button.
    const move = (event: PointerEvent) => {
      if (!event.isTrusted || event.pointerType === 'touch') return
      last = { clientX: event.clientX, clientY: event.clientY, pointerType: event.pointerType }
      // Suppress only the renderer's original move. Its button/descendants use
      // different offset origins; every move must take the same normalized path.
      if (renderer.contains(event.target as Node)) event.stopPropagation()
      const box = renderer.getBoundingClientRect()
      const nearby = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom
      if (nearby) dispatch('pointermove', last)
      else if (inside) dispatch('pointerleave', last)
      inside = nearby
    }
    const resync = () => {
      if (!last) return
      const box = renderer.getBoundingClientRect()
      const nearby = last.clientX >= box.left && last.clientX <= box.right && last.clientY >= box.top && last.clientY <= box.bottom
      if (nearby) dispatch('pointermove', last)
      else if (inside) dispatch('pointerleave', last)
      inside = nearby
    }
    document.addEventListener('pointermove', move, { passive: true, capture: true })
    const observer = new ResizeObserver(resync)
    observer.observe(renderer)
    window.addEventListener('resize', resync)
    window.addEventListener('scroll', resync, { passive: true, capture: true })
    window.visualViewport?.addEventListener('resize', resync)
    window.visualViewport?.addEventListener('scroll', resync)
    return () => {
      document.removeEventListener('pointermove', move, true)
      observer.disconnect()
      window.removeEventListener('resize', resync)
      window.removeEventListener('scroll', resync, true)
      window.visualViewport?.removeEventListener('resize', resync)
      window.visualViewport?.removeEventListener('scroll', resync)
    }
  }, [disabled, palette])

  return <span ref={root} className="analysis-spider" data-disabled={disabled || undefined}
    onPointerEnter={() => setHovered(true)} onPointerLeave={() => { setHovered(false); setPressed(false) }}
    onPointerDown={() => setPressed(true)} onPointerUp={() => setPressed(false)} onPointerCancel={() => setPressed(false)}>
    <span className="analysis-size" aria-hidden="true">{children}</span>
    {palette && <div ref={stage} className="analysis-stage" style={{ inset: `-96px -${padding}px` }}>
      <SpiderButton label="" size={42} onClick={() => { if (!disabled) onClick() }} autoplay={false} cursor="native"
        suitColor={disabled ? palette.background : pressed ? palette.foreground : hovered ? palette.highlight : palette.primary}
        webColor={hovered || pressed ? palette.background : palette.foreground} textColor={disabled ? palette.foreground : palette.background} background="transparent"
        cobwebs={!disabled} strands={disabled ? 0 : 7} reach={disabled ? 0 : 75}
        advanced={{ shine: palette.foreground, rim: 0, glint: 0, shadow: 0, grain: 0, vignette: 0, cobwebSize: 21, sense: disabled ? 0 : 125, release: 145, senseLines: !disabled, pressScale: 100, hover: disabled ? 0 : 50 }}
        font={{ fontFamily: 'Geist', fontSize: 13, fontWeight: 550, letterSpacing: 'normal', lineHeight: 1.25 }}
        style={{ minWidth: 0, minHeight: 0, width: '100%', height: '100%', overflow: 'visible', pointerEvents: 'none' }} />
    </div>}
    {button && createPortal(<span className="analysis-label">{children}</span>, button)}
  </span>
}
