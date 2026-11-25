import { useEffect, useState } from 'react'
import { api, type Collection } from './api'
import { Button } from './components/ui/button'
import { ErrorState } from './components/common'
import { navigate, logHref } from './routes'
import { MappingFields, parseAliases } from './evidence-mappings'

export function CompositeSnapshot({ collections }: { collections: Collection[] }) {
  const [selected, setSelected] = useState<string[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [services, setServices] = useState(''), [environments, setEnvironments] = useState('')
  async function create() {
    setBusy(true); setError('')
    try { const value = await api<{ id: string }>('/collections/composite', { collection_ids: selected, title: 'Combined evidence', symptom: '', mappings: { services: parseAliases(services), environments: parseAliases(environments) } }); navigate('collections', value.id) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not combine snapshots.') } finally { setBusy(false) }
  }
  if (collections.length < 2) return null
  return <details className="panel"><summary>Combine saved snapshots</summary><p>Select two to five completed snapshots. Original observations and citations remain unchanged; shared timestamps do not establish causality.</p>
    <fieldset disabled={busy}><legend>Source snapshots</legend>{collections.map(c => <label key={c.id}><input type="checkbox" checked={selected.includes(c.id)} disabled={!selected.includes(c.id) && selected.length >= 5} onChange={e => setSelected(e.target.checked ? [...selected, c.id] : selected.filter(id => id !== c.id))} />{c.title} ({c.source_type})</label>)}</fieldset>
    <fieldset disabled={busy}><legend>Correlation context</legend><MappingFields services={services} environments={environments} onServices={setServices} onEnvironments={setEnvironments} /></fieldset>
    {error && <ErrorState error={error} />}<Button disabled={busy || selected.length < 2} onClick={() => void create()}>Create combined snapshot</Button>
  </details>
}

type Graph = { edges: { id: string; source: string; target: string; relationship: string; support: string; justification: Record<string, unknown> }[]; ambiguities: { evidence_id: string; state: string }[]; omitted_edges: number; note: string }
export function EvidenceRelationships({ collection }: { collection: string }) {
  const [open, setOpen] = useState(false), [graph, setGraph] = useState<Graph | null>(null), [error, setError] = useState(''), [limit, setLimit] = useState(50)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setGraph(null); setError(''); setLimit(50)
    api<Graph>(`/collections/${collection}/relationships`, undefined, 'POST', controller.signal).then(value => {
      if (!Array.isArray(value.edges) || !Array.isArray(value.ambiguities)) throw new Error('Invalid relationship response.')
      setGraph(value)
    }).catch(e => { if (!controller.signal.aborted) setError(e.message) })
    return () => controller.abort()
  }, [open, collection])
  return <details className="panel collection-coverage" onToggle={e => setOpen(e.currentTarget.open)}><summary>Evidence relationships</summary>{error && <ErrorState error={error} />}{graph ? <><p>{graph.note}</p><ul>{graph.edges.slice(0, limit).map(edge => <li key={edge.id}><a href={logHref({ collection, evidence: edge.source })} target="_blank" rel="noopener noreferrer">Source</a> — {edge.relationship.replaceAll('_', ' ')} ({edge.support}) — <a href={logHref({ collection, evidence: edge.target })} target="_blank" rel="noopener noreferrer">Related source</a><details><summary>Relationship justification</summary><pre>{JSON.stringify(edge.justification, null, 2)}</pre></details></li>)}</ul>{graph.edges.length > limit && <Button variant="secondary" onClick={() => setLimit(n => n+50)}>Show more relationships</Button>}{graph.ambiguities.map(item => <p key={item.evidence_id}>{item.state.replaceAll('_', ' ')}: {item.evidence_id}</p>)}{!!graph.omitted_edges && <p>{graph.omitted_edges} relationships omitted by the graph bound.</p>}{!graph.edges.length && <p>No relationships established from these records.</p>}</> : open && !error && <p role="status">Loading recorded relationships…</p>}</details>
}
