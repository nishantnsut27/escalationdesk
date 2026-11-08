import { useEffect, type RefObject } from 'react'

/** Copy before React removes the hidden renderer; never replace a good frame with a blank one. */
export function retainArtworkFrame(live: HTMLDivElement | null, retained: HTMLCanvasElement | null) {
  const canvas = live?.querySelector('canvas')
  if (!canvas || !retained || !live?.dataset.painted) return
  const context = retained.getContext('2d')
  if (!context) return
  retained.width = canvas.width
  retained.height = canvas.height
  context.drawImage(canvas, 0, 0)
  retained.dataset.painted = 'true'
}

/** The downloaded renderer has no ready callback. Inspect its completed 2D frame,
 * keeping the poster/retained frame above its opaque black root until petals exist.
 * Only the small probe is read back; there is no readback during steady animation. */
export function useArtworkPaint(
  liveRef: RefObject<HTMLDivElement | null>,
  retainedRef: RefObject<HTMLCanvasElement | null>,
  active: boolean,
) {
  useEffect(() => {
    const live = liveRef.current
    if (!live || !active) return
    let frame = 0
    let attempts = 0
    const probe = document.createElement('canvas')
    probe.width = 48
    probe.height = 32
    const context = probe.getContext('2d', { willReadFrequently: true })
    const checkPaint = () => {
      frame = 0
      if (document.hidden || !context) return
      const canvas = live.querySelector('canvas')
      if (!canvas || canvas.width < 2 || canvas.height < 2) return
      try {
        context.clearRect(0, 0, probe.width, probe.height)
        context.drawImage(canvas, 0, 0, probe.width, probe.height)
        const pixels = context.getImageData(0, 0, probe.width, probe.height).data
        let petals = 0
        for (let i = 0; i < pixels.length; i += 4) {
          if (pixels[i + 3] > 200 && Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) > 40) petals++
        }
        if (petals > 24) {
          live.dataset.painted = 'true'
          retainArtworkFrame(live, retainedRef.current)
          return
        }
      } catch {
        // An unreadable or failed canvas keeps the local fallback visible.
        return
      }
      // A failed renderer must not leave a permanent polling loop. This cap
      // never reveals it; DOM/size changes can start a fresh readiness check.
      if (++attempts < 120) frame = requestAnimationFrame(checkPaint)
    }
    const restart = () => {
      delete live.dataset.painted
      cancelAnimationFrame(frame)
      attempts = 0
      frame = requestAnimationFrame(checkPaint)
    }
    const mutations = new MutationObserver(restart)
    mutations.observe(live, { childList: true, subtree: true })
    const resize = new ResizeObserver(restart)
    resize.observe(live)
    restart()
    return () => {
      cancelAnimationFrame(frame)
      mutations.disconnect()
      resize.disconnect()
      delete live.dataset.painted
    }
  }, [active, liveRef, retainedRef])
}
