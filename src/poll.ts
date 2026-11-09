/** Schedule the next refresh only after the current request has settled. */
export function poll(task: () => Promise<unknown>, interval: number | (() => number)) {
  let cancelled = false
  let running = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const available = () => (typeof document === 'undefined' || !document.hidden) && (typeof navigator === 'undefined' || navigator.onLine !== false)
  const delay = () => typeof interval === 'function' ? interval() : interval
  const schedule = () => {
    clearTimeout(timer)
    if (!cancelled && available()) timer = setTimeout(() => { void run() }, delay())
  }
  const run = async () => {
    if (cancelled || running || !available()) return
    running = true
    try { await task() }
    finally { running = false; schedule() }
  }
  const resume = () => {
    clearTimeout(timer)
    if (!cancelled && available()) void run()
  }
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', resume)
  if (typeof window !== 'undefined') { window.addEventListener('online', resume); window.addEventListener('offline', resume) }
  void run()
  return () => {
    cancelled = true; clearTimeout(timer)
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', resume)
    if (typeof window !== 'undefined') { window.removeEventListener('online', resume); window.removeEventListener('offline', resume) }
  }
}
