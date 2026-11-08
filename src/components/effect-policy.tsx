import { Component, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'

export function useMedia(query: string) {
  const [matches, setMatches] = useState(false)
  useEffect(() => {
    const media = window.matchMedia(query)
    const update = () => setMatches(media.matches)
    update(); media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [query])
  return matches
}

/** Unmount decorative renderers when hidden so their original rAF loops stop. */
export function useVisibleEffect<T extends HTMLElement>(beforeHide?: () => void) {
  const ref = useRef<T>(null)
  const beforeHideRef = useRef(beforeHide)
  beforeHideRef.current = beforeHide
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    let intersecting = false
    let wasVisible = false
    const update = () => {
      const next = intersecting && !document.hidden
      if (wasVisible && !next) beforeHideRef.current?.()
      wasVisible = next
      setVisible(next)
    }
    const observer = new IntersectionObserver(([entry]) => { intersecting = entry.isIntersecting; update() })
    observer.observe(element)
    document.addEventListener('visibilitychange', update)
    return () => { observer.disconnect(); document.removeEventListener('visibilitychange', update) }
  }, [])
  return { ref, visible }
}

export function usePalette() {
  const [palette, setPalette] = useState<{ background: string; foreground: string; primary: string; highlight: string } | null>(null)
  useEffect(() => {
    const styles = getComputedStyle(document.documentElement)
    const color = (name: string) => styles.getPropertyValue(name).trim()
    setPalette({ background: color('--background'), foreground: color('--foreground'), primary: color('--primary'), highlight: color('--destructive') })
  }, [])
  return palette
}

let supportsWebGL: boolean | undefined
export function useWebGL() {
  const [supported, setSupported] = useState(false)
  useEffect(() => {
    if (supportsWebGL === undefined) {
      try {
        const canvas = document.createElement('canvas')
        const gl = canvas.getContext('webgl')
        supportsWebGL = !!gl
        gl?.getExtension('WEBGL_lose_context')?.loseContext()
      } catch { supportsWebGL = false }
    }
    setSupported(supportsWebGL)
  }, [])
  return supported
}

export function useContextFailure(ref: RefObject<HTMLElement | null>) {
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const element = ref.current
    const fail = () => setFailed(true)
    element?.addEventListener('webglcontextlost', fail, true)
    return () => element?.removeEventListener('webglcontextlost', fail, true)
  }, [ref])
  return failed
}

export class EffectBoundary extends Component<{ children: ReactNode; fallback?: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? this.props.fallback ?? null : this.props.children }
}
