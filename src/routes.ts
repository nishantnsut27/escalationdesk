import { useEffect, useState } from 'react'
import { api, type Evidence } from './api'
import { dataCacheKey, readDataCache, requestData } from './data-cache'
import { preloadRoute } from './route-preload'

export type Route = { page: string; id: string; params: URLSearchParams }
const aliases: Record<string, string> = { overview: 'dashboard', history: 'investigations', evidence: 'collections', imports: 'import' }
const appPages = new Set(['', 'login', 'signup', 'demo-login', 'forgot', 'account', 'privacy', 'dashboard', 'collections', 'errors', 'investigations', 'incidents', 'connections', 'import', 'logs', 'demo'])
const detailPages = new Set(['collections', 'investigations', 'incidents', 'demo'])
export const navigationEvent = 'tracedesk:navigate'
export type SessionStatus = 'pending' | 'authenticated' | 'anonymous' | 'error'
export function isProtectedRoute(href: string) {
  return ['dashboard', 'collections', 'errors', 'investigations', 'incidents', 'connections', 'import', 'logs', 'demo'].includes(parseRoute(href).page)
}
export function emailDestination(href: string) {
  if (!href.startsWith('/') || href.startsWith('//') || href.includes('\\')) return ''
  const route = parseRoute(href)
  return ((['incidents', 'investigations'].includes(route.page) && route.id) || (route.page === 'logs' && route.params.get('investigation') && route.params.get('evidence'))) ? routeHref(route.page, route.id, Object.fromEntries(route.params)) : ''
}
export function destinationAfterAuth() { return '/dashboard' }
export function parseRoute(href: string): Route {
  if (href.startsWith('#auth/') || href.startsWith('/account#auth/')) return { page: 'auth-link', id: '', params: new URLSearchParams() }
  const clean = href.replace(/^#\/?|^\//, '').split('#', 1)[0]
  const [path, query = ''] = clean.split('?', 2)
  const [originalPage = '', encodedId = '', ...extra] = path.split('/')
  const page = aliases[originalPage] || originalPage
  if (!appPages.has(page) || extra.some(Boolean) || (encodedId && !detailPages.has(page))) return { page: 'not-found', id: '', params: new URLSearchParams(query) }
  let id = ''
  try { id = decodeURIComponent(encodedId) } catch { return { page: 'not-found', id: '', params: new URLSearchParams() } }
  return { page, id, params: new URLSearchParams(query) }
}
export function routeHref(page: string, id = '', parameters: Record<string, string | number | undefined | null> = {}) {
  const params = new URLSearchParams()
  Object.entries(parameters).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== '') params.set(key, String(value)) })
  return '/' + (aliases[page] || page) + (id ? '/' + encodeURIComponent(id) : '') + (params.size ? '?' + params.toString() : '')
}
export function currentRouteHref() { return window.location.pathname + window.location.search }
export function normalizeLocation() {
  const { hash, pathname, search } = window.location
  // Account fragments are captured by App before removal. Section anchors stay native.
  if (hash.startsWith('#auth/')) return
  const legacy = hash && parseRoute(hash)
  if (legacy && legacy.page !== 'not-found') {
    history.replaceState(null, '', routeHref(legacy.page, legacy.id, Object.fromEntries(legacy.params)))
    return
  }
  const route = parseRoute(pathname + search)
  if (route.page !== 'not-found' && aliases[pathname.split('/')[1]]) history.replaceState(null, '', routeHref(route.page, route.id, Object.fromEntries(route.params)) + hash)
}
export function navigateHref(href: string, replace = false) {
  if (replace) history.replaceState(null, '', href)
  else history.pushState(null, '', href)
  window.dispatchEvent(new Event(navigationEvent))
}
export function navigate(page: string, id = '', parameters: Record<string, string | number | undefined | null> = {}, replace = false) {
  navigateHref(routeHref(page, id, parameters), replace)
}
export function handleAppLink(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const anchor = event.target instanceof Element ? event.target.closest('a') : null
  if (!anchor || anchor.hasAttribute('download') || anchor.target && anchor.target !== '_self' || anchor.rel.split(' ').includes('external')) return
  const url = new URL(anchor.href, window.location.href)
  if (url.origin !== window.location.origin || !['http:', 'https:'].includes(url.protocol)) return
  const legacy = url.hash && !url.hash.startsWith('#auth/') ? parseRoute(url.hash) : null
  if (url.hash && (!legacy || legacy.page === 'not-found')) return
  const route = legacy || parseRoute(url.pathname + url.search)
  if (route.page === 'not-found') return
  event.preventDefault()
  navigateHref(legacy ? routeHref(route.page, route.id, Object.fromEntries(route.params)) : url.pathname + url.search)
}
let prefetches = 0
let lastPrefetch = 0
function preloadAppLink(event: Event) {
  const anchor = event.target instanceof Element ? event.target.closest('a') : null
  if (!anchor) return
  const url = new URL(anchor.href, window.location.href)
  if (url.origin !== window.location.origin) return
  const route = parseRoute(url.pathname + url.search)
  if (route.page !== 'not-found') void preloadRoute(route.page).catch(() => {})
  if (route.id || document.hidden || navigator.onLine === false || prefetches >= 2) return
  let path = ''
  if (route.page === 'dashboard' || route.page === 'errors') {
    const query = evidenceFilters(route.params)
    if (route.params.get('collection_id')) query.set('collection_id', route.params.get('collection_id')!)
    path = '/' + route.page + '?' + query
  } else if (route.page === 'collections') path = '/collections'
  else if (route.page === 'investigations') path = '/investigations?limit=51&offset=' + Math.max(0, Number(route.params.get('offset')) || 0) + (route.params.get('collection_id') ? '&collection_id=' + encodeURIComponent(route.params.get('collection_id')!) : '')
  if (!path || !dataCacheKey(path) || readDataCache(path)?.fresh || Date.now() - lastPrefetch < 1000) return
  lastPrefetch = Date.now(); prefetches++
  void requestData(path, signal => api(path, undefined, 'POST', signal)).catch(() => {}).finally(() => { prefetches-- })
}
export function useAppNavigation() {
  useEffect(() => {
    document.addEventListener('click', handleAppLink)
    document.addEventListener('pointerover', preloadAppLink, { passive: true })
    document.addEventListener('focusin', preloadAppLink)
    return () => {
      document.removeEventListener('click', handleAppLink)
      document.removeEventListener('pointerover', preloadAppLink)
      document.removeEventListener('focusin', preloadAppLink)
    }
  }, [])
}
export function useRoute() {
  const [href, setHref] = useState(() => { if (typeof window === 'undefined') return ''; normalizeLocation(); return currentRouteHref() })
  useEffect(() => {
    const update = () => { normalizeLocation(); setHref(currentRouteHref()) }
    for (const event of ['popstate', 'hashchange', navigationEvent]) window.addEventListener(event, update)
    update()
    return () => { for (const event of ['popstate', 'hashchange', navigationEvent]) window.removeEventListener(event, update) }
  }, [])
  return parseRoute(href)
}
export function logHref({ evidence, collection, run, investigation, row, ...filters }: {
  evidence?: string; collection?: string | null; run?: string | null; investigation?: string; row?: Evidence
  [key: string]: string | Evidence | undefined | null
}) {
  const correlation = row?.trace_id ? { trace_id: row.trace_id, request_id: null } : row?.request_id ? { request_id: row.request_id, trace_id: null } : row ? { trace_id: null, request_id: null } : {}
  return routeHref('logs', '', { ...filters as Record<string, string | undefined>, collection, run, investigation, evidence: evidence || row?.id, correlate: evidence && !row ? '1' : undefined, ...correlation })
}
export const filterKeys = ['trace_id', 'request_id', 'service', 'level', 'start_time', 'end_time', 'error_type', 'query'] as const
export function evidenceFilters(params: URLSearchParams) {
  const result = new URLSearchParams()
  for (const key of filterKeys) if (params.get(key)) result.set(key, params.get(key)!)
  return result
}
