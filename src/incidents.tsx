import { useEffect, useState, type FormEvent } from 'react'
import { Bell, Fingerprint } from 'lucide-react'
import { api, userMessage, type SentryConnection } from './api'
import { useWritePermission } from './account'
import { useData } from './workspace'
import { navigate, routeHref, useRoute } from './routes'
import { PageHeading, EmptyState, ErrorState, LoadingState, StatusBadge } from './components/common'
import { Button } from './components/ui/button'
import { AnalysisActionButton } from './components/analysis-action'
import { FullTime } from './reports'

type Settings = { enabled: boolean; projects: string[]; initial_email: boolean; result_email: boolean }
type RetainedImport = { id: string; collection_id: string; state: string; created_at: string }
type Attempt = { imports?: RetainedImport[]; id: string; number: number; state: string; explicit_retry: boolean; started_at: string | null; created_at: string; investigation_id: string | null; import_id: string | null; error: string | null }
type Notification = { id: string; kind: string; state: string; delivery_status: string | null; last_error: string | null; automatic_attempt_id: string }
type Incident = { retry_allowed?: boolean; retry_reason?: string | null; event_time_known?: boolean; id: string; issue_id: string; project_name: string; project_id: string; title: string; episode: number; trigger: string; triggered_at: string; lifecycle: string; state: string; error: string | null; resume_until: number; report_id: string | null; attempts: Attempt[]; notifications: Notification[] }
const active = ['queued', 'importing', 'investigating', 'waiting', 'deferred']

export function AutomationControls({ connection }: { connection: SentryConnection }) {
  const canWrite = useWritePermission()
  const { value, error, retry } = useData<Settings>(`/connections/sentry/${encodeURIComponent(connection.id)}/automation`)
  const [draft, setDraft] = useState<Settings | null>(null), [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [failure, setFailure] = useState('')
  useEffect(() => { if (value) setDraft(value) }, [value])
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft) return
    setBusy(true); setFailure(''); setMessage('')
    const { enabled, projects, initial_email, result_email } = draft
    try { setDraft(await api<Settings>(`/connections/sentry/${connection.id}/automation`, { enabled, projects, initial_email, result_email }, 'PUT')); setMessage('Automation settings saved.') }
    catch (e) { setFailure(userMessage(e)) } finally { setBusy(false) }
  }
  if (!draft) return error ? <ErrorState error={error} retry={retry} /> : <LoadingState>Loading automation settings</LoadingState>
  return <section className="automation-settings" aria-labelledby="automation-title">
    <div className="section-heading"><h2 id="automation-title">Automatic investigations</h2><StatusBadge state={draft.enabled ? 'enabled' : 'disabled'} /></div>
    <p>Investigate new and reopened Sentry error issues. Five automatic investigations per account per UTC day, including failed attempts. Deferred incidents resume for up to 24 hours; older work needs an explicit retry.</p>
    <form onSubmit={event => void save(event)}><fieldset disabled={!canWrite || busy}>
      <legend>Automation and notifications</legend>
      <label className="sentry-checkbox"><input type="checkbox" checked={draft.enabled} disabled={connection.state !== 'connected'} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /><span>Enable automatic investigations</span></label>
      <div className="sentry-project-options">{connection.projects.map(project => <label className="sentry-checkbox" key={project.id}><input type="checkbox" checked={draft.projects.includes(project.id)} onChange={() => setDraft({ ...draft, projects: draft.projects.includes(project.id) ? draft.projects.filter(id => id !== project.id) : [...draft.projects, project.id] })} /><span>{project.name}<small>{project.slug}</small></span></label>)}</div>
      {!connection.projects.length && <p>Check connection access to load authorized projects.</p>}
      <label className="sentry-checkbox"><input type="checkbox" checked={draft.initial_email} onChange={e => setDraft({ ...draft, initial_email: e.target.checked })} /><span>Email me when an incident is detected</span></label>
      <label className="sentry-checkbox"><input type="checkbox" checked={draft.result_email} onChange={e => setDraft({ ...draft, result_email: e.target.checked })} /><span>Email me the saved result or failure notice</span></label>
    </fieldset><p className="report-note">Evidence collection waits 60 seconds, then imports a bounded 15-minute window. Existing analysis limits apply. Disabling automation prevents new work; running investigations finish. Further analysis always needs your request.</p>
      {failure && <ErrorState error={failure} />}{message && <p role="status">{message}</p>}
      <Button disabled={!canWrite || busy || draft.enabled && !draft.projects.length} aria-busy={busy || undefined}>{busy ? 'Saving...' : 'Save automation settings'}</Button>
    </form><a className="text-link" href="/incidents">Open incidents</a>
  </section>
}

function Notifications({ items }: { items: Notification[] }) {
  return <div className="incident-notifications">{items.length ? items.map(item => <div key={item.id}><strong>{item.kind === 'incident_alert' ? 'Initial alert' : 'Investigation result'}</strong> <StatusBadge state={item.delivery_status?.replace('email.', '') || item.state} />{item.last_error && <p>{item.last_error}</p>}</div>) : <p>Notification status is being saved.</p>}</div>
}

export function IncidentsWorkspace({ id = '' }: { id?: string }) {
  const route = useRoute(), offset = Math.max(0, Number(route.params.get('offset')) || 0)
  const { value, error, retry } = useData<{ incidents: Incident[]; next_offset: number | null } | Incident>(id ? '/automatic-incidents/' + encodeURIComponent(id) : '/automatic-incidents?offset=' + offset, () => true)
  const canWrite = useWritePermission(), [busy, setBusy] = useState(false), [failure, setFailure] = useState('')
  async function requestRetry() {
    setBusy(true); setFailure('')
    try { await api('/automatic-incidents/' + id + '/retry', {}); retry() } catch (e) { setFailure(userMessage(e)) } finally { setBusy(false) }
  }
  if (!value) return error ? <ErrorState error={error} retry={retry} /> : <LoadingState layout="cards">Loading incidents</LoadingState>
  if ('incidents' in value) return <><PageHeading eyebrow="SENTRY AUTOMATION" title="Incidents" actions={<Button asChild variant="secondary"><a href="/connections">Automation settings</a></Button>}>Follow detected issues, saved assessments and email delivery. Browsing this view does not start an investigation.</PageHeading>{error && <ErrorState error={error} retry={retry} />}
    {value.incidents.length ? <div className="collection-grid">{value.incidents.map(item => <article className="panel collection-card incident-card" key={item.id}>
      <div className="section-heading"><Bell size={18} /><StatusBadge state={item.state} /></div><a href={routeHref('incidents', item.id)}><h2>{item.title}</h2></a>
      <p>{item.project_name} / issue {item.issue_id} / episode {item.episode}</p><FullTime value={item.triggered_at} /><p>Sentry lifecycle: {item.lifecycle}</p>
      {item.error && <p>{item.error}</p>}<Notifications items={item.notifications} /><div className="sentry-actions"><a href={routeHref('incidents', item.id)}>Open incident</a>{item.report_id && <a href={routeHref('investigations', item.report_id)}>View assessment</a>}</div>
    </article>)}</div> : <EmptyState title="No automatic incidents yet">Enable automation for selected projects in <a href="/connections">Connections</a>. New and genuinely reopened error issues will appear here.</EmptyState>}
    <div className="sentry-actions">{offset > 0 && <Button variant="secondary" onClick={() => navigate('incidents', '', { offset: Math.max(0, offset-50) })}>Previous</Button>}{value.next_offset !== null && <Button variant="secondary" onClick={() => navigate('incidents', '', { offset: value.next_offset })}>Next</Button>}</div></>
  const item = value
  const retryAllowed = item.retry_allowed ?? ['failed', 'interrupted', 'inconclusive'].includes(item.state)
  return <><a className="back-link" href="/incidents">Back to incidents</a><PageHeading eyebrow={`SENTRY ISSUE ${item.issue_id} / EPISODE ${item.episode}`} title={item.title} actions={item.report_id && <Button asChild><a href={routeHref('investigations', item.report_id)}>View assessment</a></Button>}>{item.project_name} / {item.trigger === 'created' ? 'New issue' : 'Reopened issue'}</PageHeading>
    {error && <ErrorState error={error} retry={retry} />}{failure && <ErrorState error={failure} />}<section className="panel incident-detail"><div className="section-heading"><h2>Incident progress</h2><StatusBadge state={item.state} /></div><p>{item.event_time_known === false ? 'Notification received' : 'Triggered'} <FullTime value={item.triggered_at} /></p><p>Sentry lifecycle: {item.lifecycle}</p>{item.error && <p>{item.error}</p>}{active.includes(item.state) && <p>Automatic resumption available until <FullTime value={new Date(item.resume_until*1000).toISOString()} />. Started investigations keep their execution limits.</p>}
    {!active.includes(item.state) && !retryAllowed && <p>{item.retry_reason || 'Open the saved assessment to analyze further.'}</p>}
    {retryAllowed && <AnalysisActionButton busy={busy} disabled={!canWrite || busy} onClick={() => void requestRetry()}><Fingerprint size={16} />{busy ? 'Submitting...' : 'Retry investigation'}</AnalysisActionButton>}</section>
    <section className="panel incident-detail"><h2>Retained attempts</h2>{item.attempts.map(attempt => <article className="incident-attempt" key={attempt.id}><div className="section-heading"><h3>Attempt {attempt.number}{attempt.explicit_retry ? ' / explicit retry' : ''}</h3><StatusBadge state={attempt.state} /></div><FullTime value={attempt.created_at} />{attempt.error && <p>{attempt.error}</p>}<div className="sentry-actions">{attempt.investigation_id && <a href={routeHref('investigations', attempt.investigation_id)}>Open saved investigation</a>}{attempt.imports?.map((snapshot, index) => <a key={snapshot.id} href={routeHref('collections', snapshot.collection_id)}>Evidence snapshot {index + 1} / {snapshot.state}</a>)}{!attempt.imports?.length && attempt.import_id && <button className="button secondary" onClick={() => { void api<{ collection_id: string }>('/sentry-imports/' + attempt.import_id).then(job => navigate('collections', job.collection_id)).catch(e => setFailure(userMessage(e))) }}>Open retained evidence</button>}</div></article>)}</section>
    <section className="panel incident-detail"><h2>Notifications</h2><Notifications items={item.notifications} /><a href="/connections">Notification settings</a></section></>
}
