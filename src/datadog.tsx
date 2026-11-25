import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, userMessage } from './api'
import { Button } from './components/ui/button'
import { ErrorState, StatusBadge } from './components/common'
import { useWritePermission } from './account'
import { navigate } from './routes'
import { poll } from './poll'
import { DatadogAutomation } from './datadog-automation'

type Connection = { id: string; provider: string; organization: string; region: string; state: string; projects: { id: string; name: string }[]; capabilities: Record<string, { state: string; reason?: string }> }
type Import = { id: string; connection_id: string; collection_id: string; state: string; error?: string }
type Workspace = { datadog_enabled: boolean; connections: Connection[]; imports: Import[] }
export function isObservabilityWorkspace(value: unknown): value is Workspace {
  if (!value || typeof value !== 'object') return false
  const data = value as Workspace
  return typeof data.datadog_enabled === 'boolean' && Array.isArray(data.connections) && Array.isArray(data.imports)
    && data.connections.every(c => typeof c.id === 'string' && typeof c.provider === 'string' && !!c.capabilities)
}

export function DatadogConnections() {
  const canWrite = useWritePermission()
  const [data, setData] = useState<Workspace | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState('')
  const lifecycle = useRef<AbortController | null>(null), revision = useRef(0), mutating = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    lifecycle.current = controller
    const stop = poll(async () => {
      if (mutating.current) return
      const currentRevision = revision.current
      try { const value = await api<unknown>('/connections', undefined, 'POST', controller.signal); if (!isObservabilityWorkspace(value)) throw new Error('Invalid connection response.'); if (currentRevision === revision.current) { setData(value); setError('') } }
      catch (e) { if (!controller.signal.aborted) setError(userMessage(e)) }
    }, 15000)
    return () => { stop(); controller.abort() }
  }, [])
  const connections = data?.connections.filter(c => c.provider === 'datadog') || []
  const current = connections.find(c => c.id === selected) || connections[0]
  async function action(path: string, body: unknown = {}, method: 'POST' | 'PUT' | 'DELETE' = 'POST') {
    if (mutating.current) return false
    revision.current++; mutating.current = true
    setBusy(true); setError('')
    try {
      await api(path, body, method, lifecycle.current?.signal)
      const value = await api<unknown>('/connections', undefined, 'POST', lifecycle.current?.signal)
      if (!isObservabilityWorkspace(value)) throw new Error('Invalid connection response.')
      setData(value)
      return true
    } catch (e) { if (!lifecycle.current?.signal.aborted) setError(userMessage(e)); return false } finally { mutating.current = false; if (!lifecycle.current?.signal.aborted) setBusy(false) }
  }
  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget
    if (await action('/connections/datadog', Object.fromEntries(new FormData(form)))) form.reset()
  }
  function importSnapshot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!current) return
    const form = new FormData(event.currentTarget)
    void action(`/connections/datadog/${current.id}/imports`, { title: 'Datadog evidence', start: form.get('start'), end: form.get('end'),
      query: form.get('query'), datasets: form.getAll('datasets'), metric_queries: String(form.get('metrics') || '').split('\n').map(s => s.trim()).filter(Boolean) })
  }
  const disabled = !canWrite || busy || !data?.datadog_enabled
  return <section className="import-panel" aria-labelledby="datadog-title"><div className="section-heading"><h2 id="datadog-title">Datadog</h2><span>Logs, spans, metrics and monitors</span></div>
    {error && <ErrorState error={error} />}{!data && !error && <p role="status">Loading Datadog connections…</p>}
    {data && !data.datadog_enabled && <p>Datadog is awaiting server enablement. Saved evidence remains available.</p>}
    <details><summary>Connect a Datadog account</summary><form onSubmit={connect}><fieldset disabled={disabled}><legend>Read-only provider access</legend><div className="observability-fields">
      <label>Connection name<input name="name" defaultValue="Datadog" maxLength={120} required /></label>
      <label>Datadog site<select name="site">{['us1','us3','us5','eu1','ap1','ap2','uk1'].map(s => <option key={s}>{s}</option>)}</select></label>
      <label>API key<input name="api_key" type="password" autoComplete="off" required /></label>
      <label>Scoped application key<input name="application_key" type="password" autoComplete="off" required /></label></div>
      <p>Keys are encrypted on the server and are not returned after setup.</p><Button type="submit">Check and connect Datadog</Button>
    </fieldset></form></details>
    {!!connections.length && <><label>Datadog connection<select value={current?.id} disabled={busy} onChange={e => setSelected(e.target.value)}>{connections.map(c => <option key={c.id} value={c.id}>{c.organization} / {c.region.toUpperCase()}</option>)}</select></label>
      {current && <><StatusBadge state={current.state} /><ul>{Object.entries(current.capabilities).map(([kind, coverage]) => <li key={kind}>{kind}: {coverage.state}{coverage.reason ? ' — ' + coverage.reason : ''}</li>)}</ul>
      <div className="sentry-actions"><Button variant="secondary" disabled={disabled} onClick={() => void action(`/connections/datadog/${current.id}/check`)}>Check access</Button><Button variant="secondary" disabled={disabled} onClick={() => void action(`/connections/datadog/${current.id}`, {}, 'DELETE')}>Disconnect</Button></div>
      <form key={current.id} onSubmit={importSnapshot}><fieldset disabled={disabled || data?.imports.some(i => ['queued','running','rate_limited'].includes(i.state))}><legend>Save a bounded evidence snapshot</legend>
        <div className="observability-fields"><label>Start time (ISO 8601)<input name="start" defaultValue={new Date(Date.now()-15*60000).toISOString()} required /></label><label>End time (ISO 8601)<input name="end" defaultValue={new Date().toISOString()} required /></label><label>Logs and spans query<input name="query" defaultValue="service:" required /></label></div>
        <div className="sentry-actions">{['logs','spans','metrics','monitors'].map(kind => <label key={kind}><input name="datasets" value={kind} type="checkbox" defaultChecked={['logs','spans'].includes(kind)} />{kind}</label>)}</div>
        <label>Metric queries (one per line, up to five)<textarea name="metrics" /></label><p>At most 24 hours and 5,000 observations. Importing does not start an investigation.</p><Button type="submit">Save snapshot</Button>
      </fieldset></form>
      <DatadogAutomation key={current.id} id={current.id} sources={data?.connections.filter(c => c.provider === 'sentry' && c.state === 'connected') || []} disabled={disabled} /></>}
    </>}
    {data?.imports.filter(i => connections.some(c => c.id === i.connection_id)).map(item => <div className="connection-import" key={item.id}><StatusBadge state={item.state} /><Button variant="secondary" onClick={() => navigate('collections', item.collection_id)}>Open snapshot</Button>{item.error && <p>{item.error}</p>}{['failed','partial','interrupted','cancelled'].includes(item.state) && <Button disabled={disabled} onClick={() => void action(`/provider-imports/${item.id}/retry`)}>Retry import</Button>}{['queued','running','rate_limited'].includes(item.state) && <Button disabled={disabled} onClick={() => void action(`/provider-imports/${item.id}/cancel`)}>Cancel import</Button>}</div>)}
  </section>
}
