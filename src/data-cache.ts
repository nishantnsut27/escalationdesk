export type DataCacheScope = { account: string; permission: string }

type CacheEntry = {
  key: string
  path: string
  value: unknown
  storedAt: number
  accessedAt: number
  bytes: number
  persisted: boolean
}

type StoredCache = {
  version: 1
  scope: DataCacheScope
  entries: Array<Pick<CacheEntry, 'path' | 'value' | 'storedAt' | 'accessedAt'>>
}

type InFlight = { promise: Promise<unknown>; controller: AbortController }

export const DATA_FRESH_MS = 30_000
export const DATA_STALE_MS = 5 * 60_000
const MAX_ENTRIES = 50
const MAX_BYTES = 1024 * 1024
const STORAGE_PREFIX = 'tracedesk:data-cache:v1:'
const AUTH_CHANNEL = 'tracedesk:authentication:v1'
const AUTH_STORAGE_EVENT = 'tracedesk:authentication-event'

let scope: DataCacheScope | null = null
const entries = new Map<string, CacheEntry>()
const requests = new Map<string, InFlight>()
let revision = 0
const listeners = new Set<() => void>()
export const dataCacheRevision = () => revision
export function subscribeDataCache(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
function changed() { revision++; for (const listener of listeners) listener() }

function storage() {
  try { return typeof sessionStorage === 'undefined' ? null : sessionStorage }
  catch { return null }
}

function bytes(value: unknown) {
  try {
    const text = JSON.stringify(value)
    return typeof TextEncoder === 'undefined' ? text.length * 2 : new TextEncoder().encode(text).byteLength
  } catch { return MAX_BYTES + 1 }
}

export function canonicalDataPath(path: string) {
  const url = new URL(path, 'https://tracedesk.local')
  const sorted = [...url.searchParams.entries()].sort(([leftKey, leftValue], [rightKey, rightValue]) =>
    leftKey.localeCompare(rightKey) || leftValue.localeCompare(rightValue))
  const query = new URLSearchParams(sorted).toString()
  return url.pathname + (query ? '?' + query : '')
}

function scopeKey(value = scope) {
  return value ? `${value.account}:${value.permission}` : ''
}

function storageKey(value = scope) {
  return STORAGE_PREFIX + scopeKey(value)
}

export function dataCacheKey(path: string) {
  const current = scopeKey()
  return current ? `${current}:${canonicalDataPath(path)}` : ''
}

function persistable(path: string) {
  const normalized = canonicalDataPath(path)
  const pathname = normalized.split('?', 1)[0]
  if (pathname === '/dashboard' || pathname === '/errors' || pathname === '/collections') return true
  if (pathname === '/investigations' || pathname === '/automatic-incidents') return true
  return false
}

function removeStoredCaches(keep = '') {
  const target = storage()
  if (!target) return
  try {
    for (let index = target.length - 1; index >= 0; index--) {
      const key = target.key(index)
      if (key?.startsWith(STORAGE_PREFIX) && key !== keep) target.removeItem(key)
    }
  } catch { /* Browser storage is an optional acceleration. */ }
}

function trim() {
  let total = [...entries.values()].reduce((sum, entry) => sum + entry.bytes, 0)
  const ordered = [...entries.values()].sort((left, right) => left.accessedAt - right.accessedAt)
  while ((entries.size > MAX_ENTRIES || total > MAX_BYTES) && ordered.length) {
    const entry = ordered.shift()!
    if (entries.delete(entry.key)) total -= entry.bytes
  }
}

function persist() {
  if (!scope) return
  const target = storage()
  if (!target) return
  try {
    const stored: StoredCache = {
      version: 1,
      scope,
      entries: [...entries.values()].filter(entry => entry.persisted)
        .sort((left, right) => right.accessedAt - left.accessedAt)
        .map(({ path, value, storedAt, accessedAt }) => ({ path, value, storedAt, accessedAt })),
    }
    while (stored.entries.length && bytes(stored) > MAX_BYTES) stored.entries.pop()
    target.setItem(storageKey(), JSON.stringify(stored))
  } catch { /* Memory cache remains available. */ }
}

function restore() {
  if (!scope) return
  const target = storage()
  if (!target) return
  try {
    const raw = target.getItem(storageKey())
    if (!raw) return
    if (raw.length > MAX_BYTES) throw new Error()
    const stored = JSON.parse(raw) as StoredCache
    if (stored.version !== 1 || stored.scope.account !== scope.account || stored.scope.permission !== scope.permission || !Array.isArray(stored.entries)) throw new Error()
    const now = Date.now()
    for (const item of stored.entries) {
      if (!item || typeof item.path !== 'string' || !Number.isFinite(item.storedAt) || item.storedAt > now || now - item.storedAt >= DATA_STALE_MS || !persistable(item.path)) continue
      const key = dataCacheKey(item.path), size = bytes(item.value)
      if (!key || size > MAX_BYTES) continue
      entries.set(key, { key, path: canonicalDataPath(item.path), value: item.value, storedAt: item.storedAt,
        accessedAt: typeof item.accessedAt === 'number' ? item.accessedAt : item.storedAt, bytes: size, persisted: true })
    }
    trim()
  } catch {
    try { target.removeItem(storageKey()) } catch { /* Ignore unavailable storage. */ }
  }
}

export function setDataCacheScope(next: DataCacheScope | null) {
  if (!next) { clearDataCache(); return }
  const previous = scope
  if (scopeKey(previous) === scopeKey(next)) return
  for (const request of requests.values()) request.controller.abort()
  requests.clear(); entries.clear()
  if (next) removeStoredCaches(previous ? '' : storageKey(next))
  scope = next
  if (scope) restore()
  changed()
}

export function clearDataCache(removeStorage = true) {
  for (const request of requests.values()) request.controller.abort()
  requests.clear(); entries.clear(); scope = null
  if (removeStorage) removeStoredCaches()
  changed()
}

export function invalidateDataCache() {
  for (const request of requests.values()) request.controller.abort()
  requests.clear(); entries.clear()
  const target = storage()
  if (target && scope) try { target.removeItem(storageKey()) } catch { /* Memory invalidation already succeeded. */ }
  changed()
}

export function readDataCache<T>(path: string) {
  const key = dataCacheKey(path)
  if (!key) return null
  const entry = entries.get(key)
  if (!entry) return null
  const age = Date.now() - entry.storedAt
  if (age < 0 || age >= DATA_STALE_MS) {
    entries.delete(key); persist()
    return null
  }
  entry.accessedAt = Date.now()
  return { value: entry.value as T, age, storedAt: entry.storedAt, fresh: age < DATA_FRESH_MS }
}

export function seedDataCache<T>(path: string, value: T, storedAt = Date.now()) {
  const key = dataCacheKey(path)
  if (!key) return
  const size = bytes(value)
  if (size > MAX_BYTES) return
  const stamp = Date.now()
  if (stamp - storedAt >= DATA_STALE_MS || storedAt > stamp || (entries.get(key)?.storedAt ?? 0) > storedAt) return
  entries.set(key, { key, path: canonicalDataPath(path), value, storedAt, accessedAt: stamp, bytes: size, persisted: persistable(path) })
  trim(); persist()
}

export function requestData<T>(path: string, loader: (signal: AbortSignal) => Promise<T>, force = false): Promise<T> {
  const key = dataCacheKey(path)
  if (!key) return loader(new AbortController().signal)
  const existing = requests.get(key)
  if (existing && !force) return existing.promise as Promise<T>
  if (existing && force) existing.controller.abort()
  const controller = new AbortController()
  const promise = loader(controller.signal).then(value => {
    // Aborted loaders can still resolve (including after a scope switch back).
    // Compare request identity, which is never reused by invalidation or retry.
    if (controller.signal.aborted || requests.get(key)?.controller !== controller || dataCacheKey(path) !== key) throw new DOMException('Read superseded', 'AbortError')
    seedDataCache(path, value)
    if (canonicalDataPath(path).split('?')[0] === '/dashboard') {
      const collections = (value as { collections?: unknown }).collections
      if (Array.isArray(collections)) seedDataCache('/collections', collections)
    }
    return value
  }).finally(() => {
    if (requests.get(key)?.controller === controller) requests.delete(key)
  })
  requests.set(key, { promise, controller })
  return promise
}

function postAuthChange() {
  if (typeof window === 'undefined') return
  try {
    const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(AUTH_CHANNEL)
    if (channel) { channel.postMessage({ changedAt: Date.now() }); channel.close(); return }
  } catch { /* localStorage remains as a fallback. */ }
  try {
    localStorage.setItem(AUTH_STORAGE_EVENT, String(Date.now()) + ':' + Math.random())
    localStorage.removeItem(AUTH_STORAGE_EVENT)
  } catch { /* Cross-tab notification is best effort. */ }
}

export function broadcastAuthenticationChange() { postAuthChange() }

export function subscribeAuthenticationChanges(listener: () => void) {
  if (typeof window === 'undefined') return () => {}
  let channel: BroadcastChannel | null = null
  try {
    if (typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel(AUTH_CHANNEL)
      channel.addEventListener('message', listener)
    }
  } catch { channel = null }
  const stored = (event: StorageEvent) => { if (event.key === AUTH_STORAGE_EVENT && event.newValue) listener() }
  window.addEventListener('storage', stored)
  return () => { channel?.close(); window.removeEventListener('storage', stored) }
}
