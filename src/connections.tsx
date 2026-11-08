import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Cable, ExternalLink, LoaderCircle, RefreshCw, Unplug, Download } from 'lucide-react'
import { api, type SentryConnection, type SentryImport, type SentryWorkspace } from './api'
import { PageHeading, LoadingState, StatusBadge, ErrorState } from './components/common'
import { Button } from './components/ui/button'
import { SentryConnectionEmptyState } from './components/sentry-empty-state'
import { CupLoader } from './components/ui/cup-loader'
import { ProgressBar } from './components/ui/progress-bar'
import { navigate } from './routes'
import { poll } from './poll'
import { FullTime } from './reports'
import { useWritePermission } from './account'
import { AutomationControls } from './incidents'

const active = ['queued', 'running', 'rate_limited']
const incomplete = ['failed', 'partial', 'interrupted', 'cancelled']
const localTime = (date: Date) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)

export function isSentryWorkspace(value: unknown): value is SentryWorkspace {
  if (!value || typeof value !== 'object') return false
  const data = value as Partial<SentryWorkspace>
  return typeof data.configured === 'boolean' && typeof data.public_url === 'string' && Array.isArray(data.connections) && Array.isArray(data.imports)
}

export function ConnectionRegion({ region }: { region: SentryConnection['region'] }) {
  return <p>Connection region: {region === 'eu' ? 'European Union (EU)' : 'United States (US)'}.</p>
}

export function SentrySetup({ configured, publicUrl }: { configured: boolean; publicUrl: string }) {
  return <section className="import-panel sentry-setup">
    <div className="section-heading"><h2><Cable size={18} />Connect your application through Sentry</h2><span>Read access</span></div>
    <ol><li>Add Sentry to your application and send errors, logs and traces to your own project.</li><li>Connect Sentry here, approve access and select your projects.</li><li>Import a time window, review the evidence and click Investigate.</li></ol>
    <p>A website URL alone cannot provide backend logs. Your application's Sentry DSN sends telemetry; the authorization here lets TraceDesk read it.</p>
    <a href="https://docs.sentry.io/platforms/" target="_blank" rel="noreferrer">Application SDK setup <ExternalLink size={13} /></a>
    {!configured && <p className="import-guidance" role="status">Sentry connection setup is pending on this server. The operator must configure the public integration, HTTPS address and encrypted credential storage. Saved collections remain available.</p>}
    {publicUrl && globalThis.location?.origin !== publicUrl && <p className="import-guidance">For authorization, <a href={publicUrl}>open the configured HTTPS workspace</a> and sign in there.</p>}
    <p className="report-note">Sentry's approval is required for organizations outside the integration developer's organization. Connecting or importing never starts model analysis.</p>
  </section>
}

export function SentryImportCard({ item, busy, onAction, onOpen }: { item: SentryImport; busy: boolean; onAction: (action: string) => void; onOpen: () => void }) {
  const canWrite = useWritePermission()
  return <article className="sentry-import-card">
    <div><strong>{item.parameters.title}</strong><StatusBadge state={item.state} /></div>
    <p>{item.progress.observations || 0} observations retained / <FullTime value={item.created_at} /></p>
    {item.error && <ErrorState error={item.error} />}
    {active.includes(item.state) ? <div role="status">{item.state !== 'running' && <CupLoader />}<ProgressBar label={item.state === 'rate_limited' ? 'Waiting to retry import' : item.state === 'queued' ? 'Import queued' : 'Importing evidence'} description={<>{item.progress.message}{' · '}{item.progress.observations || 0} observations retained</>} isIndeterminate /></div> : item.progress.message && <p>{item.progress.message}</p>}
    {item.progress.retry_at && <p>Next retry: <FullTime value={new Date(item.progress.retry_at * 1000).toISOString()} /></p>}
    <div className="sentry-actions"><button className="button secondary" onClick={onOpen}>Review evidence</button>
      {active.includes(item.state) && <button className="button secondary" disabled={!canWrite || busy} onClick={() => onAction('cancel')}>Cancel import</button>}
      {incomplete.includes(item.state) && <button className="button secondary" disabled={!canWrite || busy} onClick={() => onAction('retry')}>Retry as new snapshot</button>}
    </div>
  </article>
}

function ImportForm({ connection, disabled, onSubmit }: { connection: SentryConnection; disabled: boolean; onSubmit: (data: unknown) => Promise<void> }) {
  const [projects, setProjects] = useState<string[]>([])
  const [datasets, setDatasets] = useState(['errors', 'logs', 'spans'])
  const [start, setStart] = useState(() => localTime(new Date(Date.now() - 3600000)))
  const [end, setEnd] = useState(() => localTime(new Date()))
  const [title, setTitle] = useState('Sentry evidence')
  const [symptom, setSymptom] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    await onSubmit({ projects, datasets, start: new Date(start).toISOString(), end: new Date(end).toISOString(), title, symptom })
  }
  const toggle = (values: string[], id: string) => values.includes(id) ? values.filter(v => v !== id) : [...values, id]
  return <form className="sentry-import-form" onSubmit={e => void submit(e)}>
    {connection.projects.length ? <fieldset disabled={disabled}><legend>Projects</legend><div className="sentry-project-options">{connection.projects.map(project => <label className="sentry-checkbox" key={project.id}><input type="checkbox" checked={projects.includes(project.id)} onChange={() => setProjects(toggle(projects, project.id))} /><span>{project.name} <small>{project.slug}</small></span></label>)}</div></fieldset> : <SentryConnectionEmptyState variant="projects" />}
    <fieldset disabled={disabled}><legend>Evidence to import</legend><div className="sentry-dataset-options">{['errors', 'logs', 'spans'].map(dataset => <label className="sentry-checkbox" key={dataset}><input type="checkbox" checked={datasets.includes(dataset)} onChange={() => setDatasets(toggle(datasets, dataset))} /><span>{dataset}</span><small>{connection.capabilities[dataset]?.state || 'Access not checked'}</small></label>)}</div></fieldset>
    {Object.values(connection.capabilities).some(c => c.project) && <p className="report-note">Access check samples the first authorized project. Each selected project is checked again during import; an empty dataset may receive evidence later.</p>}
    <div className="import-fields"><label>Start (your local time)<input type="datetime-local" required value={start} onChange={e => setStart(e.target.value)} disabled={disabled} /></label><label>End (your local time)<input type="datetime-local" required value={end} onChange={e => setEnd(e.target.value)} disabled={disabled} /></label></div>
    <label>Collection title<input required maxLength={120} value={title} onChange={e => setTitle(e.target.value)} disabled={disabled} /></label>
    <label>What did you observe? (optional)<textarea maxLength={2000} rows={2} value={symptom} onChange={e => setSymptom(e.target.value)} disabled={disabled} /></label>
    <p className="report-note">Up to 24 hours and 5,000 observations per snapshot. API availability, retention, sampling and import limits are shown with the saved evidence. Imports use your Sentry allowance.</p>
    <button className="button primary" disabled={disabled || !projects.length || !datasets.length}><Download size={16} />Import evidence</button>
  </form>
}

export function ConnectionsWorkspace() {
  const canWrite = useWritePermission()
  const [data, setData] = useState<SentryWorkspace | null>(null)
  const [selected, setSelected] = useState('')
  const [region, setRegion] = useState('us')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const mounted = useRef(false)
  const refreshInFlight = useRef<Promise<void> | null>(null)
  const refresh = useCallback(() => {
    if (refreshInFlight.current) return refreshInFlight.current
    const request = api<unknown>('/connections/sentry').then(value => {
      if (!isSentryWorkspace(value)) throw new Error('Sentry connections could not be loaded. Try again.')
      if (mounted.current) {
        setData(value); setLoadError('')
        setSelected(id => value.connections.some(connection => connection.id === id) ? id : value.connections[0]?.id || '')
      }
    }).finally(() => { refreshInFlight.current = null })
    refreshInFlight.current = request
    return request
  }, [])
  const fail = useCallback((e: unknown) => {
    if (!mounted.current) return
    setError(e instanceof Error ? e.message : 'Sentry request failed.')
  }, [])
  useEffect(() => {
    mounted.current = true
    const stop = poll(() => refresh().catch(e => { if (mounted.current) setLoadError(e instanceof Error ? e.message : 'Sentry connections could not be loaded. Try again.') }), 4000)
    return () => { mounted.current = false; stop() }
  }, [refresh])
  async function retryConnections() {
    setLoadError('')
    try { await refresh() } catch (e) { if (mounted.current) setLoadError(e instanceof Error ? e.message : 'Sentry connections could not be loaded. Try again.') }
  }
  async function action(path: string, body: unknown = {}) {
    setBusy(true); setError('')
    try { await api(path, body); await refresh() } catch (e) { fail(e) } finally { if (mounted.current) setBusy(false) }
  }
  async function connect() {
    setBusy(true); setError('')
    try {
      const value = await api<{ url: string }>('/connections/sentry/start', { region })
      if (mounted.current) window.location.assign(value.url)
    } catch (e) { fail(e) } finally { if (mounted.current) setBusy(false) }
  }
  const current = data?.connections.find(c => c.id === selected)
  const activeImport = data?.imports.some(item => active.includes(item.state))
  return <div className="connections-workspace"><PageHeading eyebrow="CONNECTIONS" title="Connections" actions={<div className="connection-header-actions"><label>Region for new authorization<select value={region} onChange={e => setRegion(e.target.value)} disabled={busy || !data}><option value="us">United States</option><option value="eu">European Union</option></select></label><Button onClick={() => void connect()} disabled={!canWrite || busy || !data?.configured} aria-busy={busy || undefined}>{busy ? <LoaderCircle size={16} className="spin" /> : <Cable size={16} />}Connect Sentry</Button></div>}>Connect Sentry, select a time window and save the observations you need.</PageHeading>
    {error && <ErrorState error={error} />}
    {loadError && <ErrorState error={loadError} retry={() => void retryConnections()} />}
    {!data && !loadError && <LoadingState>Loading Sentry connections…</LoadingState>}
    {data && <>
      {!data.connections.length && <SentryConnectionEmptyState variant={data.configured ? 'empty' : 'setup'} />}
      <details className="sentry-setup-details"><summary>Connection setup &amp; data use</summary><SentrySetup configured={data.configured} publicUrl={data.public_url} /></details>
    {!!data.connections.length && <section className="import-panel">
      {!!data?.connections.length && <label>Connection<select value={selected} onChange={e => setSelected(e.target.value)}>{data.connections.map(c => <option key={c.id} value={c.id}>{c.organization} / {c.region.toUpperCase()} / {c.state}</option>)}</select></label>}
      {current && <><div className="section-heading"><h2>{current.organization}</h2><StatusBadge state={current.state} /></div>
        <ConnectionRegion region={current.region} />
        <AutomationControls key={current.id} connection={current} />
        {current.error && <ErrorState error={current.error} />}
        {current.checked_at && <p>Last checked <FullTime value={current.checked_at} /></p>}
        <div className="sentry-actions"><button className="button secondary" disabled={!canWrite || busy || current.state !== 'connected'} onClick={() => void action(`/connections/sentry/${current.id}/check`)}><RefreshCw size={15} />Check access</button><button className="button secondary" disabled={!canWrite || busy || current.state === 'disconnected'} onClick={() => void action(`/connections/sentry/${current.id}/disconnect`)}><Unplug size={15} />Disconnect</button></div>
        {current.state !== 'connected' ? <p>Use Connect Sentry to authorize this organization again. Saved snapshots remain readable. Local disconnect does not uninstall the integration in Sentry.</p> : <ImportForm key={current.id} connection={current} disabled={!canWrite || busy || !!activeImport} onSubmit={body => action(`/connections/sentry/${current.id}/imports`, body)} />}
      </>}
    </section>}
    <section className="import-panel"><div className="section-heading"><h2>Recent imports</h2><span>Saved snapshots</span></div>{data?.imports.map(item => <SentryImportCard key={item.id} item={item} busy={busy} onOpen={() => navigate('collections', item.collection_id)} onAction={kind => void action(`/sentry-imports/${item.id}/${kind}`)} />)}{data && !data.imports.length && <p>Your imports will appear here. Manual imports need an explicit investigation. Enabled incident automation collects and investigates qualifying Sentry issues.</p>}</section>
    </>}
  </div>
}
