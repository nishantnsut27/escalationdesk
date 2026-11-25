import { useEffect, useState } from 'react'
import { api, type Hypothesis } from './api'
import { Citations } from './reports'
import { ErrorState } from './components/common'

export function HypothesisPanel({ hypotheses, stopReason }: { hypotheses?: Hypothesis[]; stopReason?: string }) {
  if (!hypotheses?.length) return null
  return <section className="report-section"><h3>Hypothesis tracking</h3>{hypotheses.map(h => <article className="hypothesis-card" key={h.id}>
    <p><strong>{h.description}</strong></p><p>{h.status} · {h.support_level.replaceAll('_', ' ')}</p><p>{h.transition_reason}</p>
    <Citations ids={h.supporting_evidence_ids} label="Supporting sources" /><Citations ids={h.contradicting_evidence_ids} label="Contradicting sources" />
    {!!h.missing_evidence.length && <><h4>Still unknown</h4><ul>{h.missing_evidence.map((gap, i) => <li key={i}>{gap}</li>)}</ul></>}
  </article>)}{stopReason && <p>Investigation stopped: {stopReason}</p>}<p>Support labels describe evidence strength, not calibrated probabilities.</p></section>
}

type Event = { sequence: number; kind: string; created_at: string; payload: Record<string, unknown> }
type Recording = { recorded: boolean; integrity: string; failures?: string[]; events?: Event[]; uncertain_requests?: number[]; checkpoint_stage?: string; note: string }
export function ExecutionPlayback({ id }: { id: string }) {
  const [open, setOpen] = useState(false), [recording, setRecording] = useState<Recording | null>(null), [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setRecording(null); setError('')
    api<Recording>(`/investigations/${id}/replay`, undefined, 'POST', controller.signal).then(value => {
      if (typeof value.recorded !== 'boolean' || value.recorded && !Array.isArray(value.events)) throw new Error('Invalid recorded execution response.')
      setRecording(value)
    }).catch(e => { if (!controller.signal.aborted) setError(e.message) })
    return () => controller.abort()
  }, [open, id])
  return <details className="report-section" onToggle={e => setOpen(e.currentTarget.open)}><summary>Recorded investigation steps</summary><p>Playback uses saved records and makes no model or observability-provider calls.</p>{error && <ErrorState error={error} />}
    {recording && <><p>{recording.note}</p>{recording.recorded && <><p>Recording integrity: {recording.integrity}. Last checkpoint: {recording.checkpoint_stage?.replaceAll('_', ' ') || 'unavailable'}.</p>
      {!!recording.uncertain_requests?.length && <p>Requests with an unknown outcome: {recording.uncertain_requests.join(', ')}. They will not be resent automatically.</p>}
      {recording.failures?.map(f => <p key={f}>{f}</p>)}<a className="button secondary" href={`/api/investigations/${encodeURIComponent(id)}/replay?export=1`} download>Download recorded execution</a>
      <ol>{recording.events?.map(e => <li key={e.sequence}><details><summary>{e.kind.replaceAll('_', ' ')} <time>{e.created_at}</time></summary><pre>{JSON.stringify(e.payload, null, 2)}</pre></details></li>)}</ol></>}</>}
    {open && !recording && !error && <p role="status">Checking recorded execution…</p>}
  </details>
}
