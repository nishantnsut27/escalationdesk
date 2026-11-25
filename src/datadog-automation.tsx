import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api } from './api'
import { Button } from './components/ui/button'
import { ErrorState, StatusBadge } from './components/common'
import { MappingFields, parseAliases, formatAliases, type Mappings } from './evidence-mappings'

type Source = { id: string; organization: string; projects: { id: string; name: string }[] }
type Settings = { enabled: boolean; initial_email: boolean; result_email: boolean; webhook_configured: boolean; webhook_url: string; webhook_secret?: string;
  configuration: { monitor_ids?: string[]; metric_queries?: string[]; mappings?: Mappings; enrichment?: { connection_id: string; project_ids: string[] } | null } }
const webhookPayload = JSON.stringify({ monitor_id: '$ALERT_ID', cycle_key: '$ALERT_CYCLE_KEY', scope: '$ALERT_SCOPE', transition: '$ALERT_TRANSITION', timestamp: '$DATE', title: '$EVENT_TITLE' }, null, 2)

export function DatadogAutomation({ id, sources, disabled }: { id: string; sources: Source[]; disabled: boolean }) {
  const [settings, setSettings] = useState<Settings | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false), [notice, setNotice] = useState('')
  const [monitors, setMonitors] = useState(''), [metrics, setMetrics] = useState(''), [source, setSource] = useState(''), [projects, setProjects] = useState<string[]>([])
  const [initial, setInitial] = useState(true), [result, setResult] = useState(true), [enabled, setEnabled] = useState(false)
  const [services, setServices] = useState(''), [environments, setEnvironments] = useState(''), [secret, setSecret] = useState('')
  const requests = useRef<AbortController | null>(null)
  function accept(value: Settings) {
    if (typeof value.enabled !== 'boolean' || !value.configuration) throw new Error('Invalid automation settings response.')
    setSettings(value); setEnabled(value.enabled); setInitial(value.initial_email); setResult(value.result_email)
    setMonitors((value.configuration.monitor_ids || []).join(', ')); setMetrics((value.configuration.metric_queries || []).join('\n'))
    setSource(value.configuration.enrichment?.connection_id || ''); setProjects(value.configuration.enrichment?.project_ids || [])
    setServices(formatAliases(value.configuration.mappings?.services)); setEnvironments(formatAliases(value.configuration.mappings?.environments))
    if (value.webhook_secret) setSecret(value.webhook_secret)
  }
  useEffect(() => {
    const controller = new AbortController(); requests.current = controller
    api<Settings>(`/connections/datadog/${id}/automation`, undefined, 'POST', controller.signal).then(accept).catch(e => { if (!controller.signal.aborted) setError(e.message) })
    return () => controller.abort()
  }, [id])
  async function save(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice(''); setBusy(true)
    try {
      const value = await api<Settings>(`/connections/datadog/${id}/automation`, {
        enabled, initial_email: initial, result_email: result, monitor_ids: monitors.split(',').map(s => s.trim()).filter(Boolean),
        metric_queries: metrics.split('\n').map(s => s.trim()).filter(Boolean), enrichment: source ? { connection_id: source, project_ids: projects } : null,
        mappings: { services: parseAliases(services), environments: parseAliases(environments) },
      }, 'PUT', requests.current?.signal)
      accept(value); setNotice(value.enabled ? 'Monitor automation saved.' : 'Monitor automation disabled. Your selections are retained.')
    } catch (e) { if (!requests.current?.signal.aborted) setError(e instanceof Error ? e.message : 'Could not save settings.') }
    finally { if (!requests.current?.signal.aborted) setBusy(false) }
  }
  const selectedSource = sources.find(s => s.id === source)
  return <details><summary>Monitor-triggered investigations</summary>
    {error && <ErrorState error={error} />}{notice && <p role="status">{notice}</p>}
    {!settings ? <p role="status">{error ? 'Settings could not be loaded. Reload this page to retry.' : 'Loading monitor settings…'}</p> : <>
      <StatusBadge state={settings.enabled ? 'enabled' : 'disabled'} /><form onSubmit={e => void save(e)}><fieldset disabled={disabled || busy}><legend>Automatic investigation settings</legend>
        <label><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />Enable selected monitors</label>
        <label>Monitor IDs (comma separated)<input value={monitors} onChange={e => setMonitors(e.target.value)} required={enabled} /></label>
        <label><input type="checkbox" checked={initial} onChange={e => setInitial(e.target.checked)} />Initial email</label><label><input type="checkbox" checked={result} onChange={e => setResult(e.target.checked)} />Result email</label>
        <label>Automatic metric queries (one per line, up to five)<textarea value={metrics} onChange={e => setMetrics(e.target.value)} /></label>
        <label>Sentry enrichment<select value={source} onChange={e => { setSource(e.target.value); setProjects([]) }}><option value="">Datadog evidence only</option>{sources.map(s => <option key={s.id} value={s.id}>{s.organization}</option>)}{source && !selectedSource && <option value={source}>Selected connection unavailable</option>}</select></label>
        {selectedSource && <fieldset><legend>Enrichment projects</legend>{selectedSource.projects.map(p => <label key={p.id}><input type="checkbox" checked={projects.includes(p.id)} onChange={e => setProjects(e.target.checked ? [...projects, p.id] : projects.filter(id => id !== p.id))} />{p.name}</label>)}</fieldset>}
        <MappingFields services={services} environments={environments} onServices={setServices} onEnvironments={setEnvironments} />
        <p>Automatic investigations share the five-per-day account allowance. Include service and environment in monitor scopes. Enrichment shares the same collection budget and may be partial.</p>
        <Button type="submit">{busy ? 'Saving…' : 'Save monitor settings'}</Button>
      </fieldset></form>
      <details><summary>Datadog webhook setup</summary><label>Webhook URL<input readOnly value={settings.webhook_url} /></label>
        {secret ? <div role="status"><p>Copy this secret into a hidden Datadog webhook variable. It is shown only in this session.</p><label>Webhook secret<input type="password" readOnly value={secret} onFocus={e => e.currentTarget.select()} /></label></div> : <p>{settings.webhook_configured ? 'A secret is configured. Use the value saved during setup.' : 'Save settings to generate a webhook secret.'}</p>}
        <p>Set the X-TraceDesk-Webhook-Token header to your hidden variable. Use this custom JSON payload and add the webhook to the selected monitor notifications.</p><pre>{webhookPayload}</pre>
        <a href="https://docs.datadoghq.com/integrations/webhooks/" target="_blank" rel="noreferrer">Datadog webhook instructions</a>
      </details>
    </>}
  </details>
}
