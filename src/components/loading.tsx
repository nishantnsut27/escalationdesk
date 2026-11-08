import { useEffect } from 'react'
import { Skeleton } from './ui/skeleton'

export type LoadingLayout = 'dashboard' | 'errors' | 'cards' | 'demo' | 'investigations' | 'collection' | 'observations' | 'histogram' | 'counts' | 'timeline' | 'flow' | 'sources' | 'record' | 'detail'

export function useLoadingMotion() {
  useEffect(() => {
    const update = () => { document.documentElement.dataset.uiHidden = String(document.hidden) }
    update(); document.addEventListener('visibilitychange', update)
    return () => { document.removeEventListener('visibilitychange', update); delete document.documentElement.dataset.uiHidden }
  }, [])
}
const lines = (count = 3) => Array.from({ length: count }, (_, i) => <Skeleton key={i} className={'skeleton-line ' + (i % 3 === 0 ? 'skeleton-short' : '')} />)
const chart = <div className="skeleton-chart">{[35, 62, 45, 80, 55, 92, 68, 48, 72, 39, 60, 44].map((height, i) => <Skeleton key={i} className="skeleton-bar" style={{ height: height + '%' }} />)}</div>
const card = (i: number) => <div key={i} className="card skeleton-card"><Skeleton className="skeleton-icon" />{lines(2)}<Skeleton className="skeleton-paragraph" /><div className="skeleton-pills"><Skeleton /><Skeleton /></div><Skeleton className="skeleton-line" /></div>
const rows = (count = 5) => <div className="skeleton-rows">{Array.from({ length: count }, (_, i) => <div className="skeleton-row" key={i}><Skeleton className="skeleton-icon" /><div>{lines(2)}</div><Skeleton className="skeleton-pill" /></div>)}</div>

export function LayoutSkeleton({ layout = 'detail', count = 3 }: { layout?: LoadingLayout; count?: number }) {
  switch (layout) {
    case 'dashboard': return <div className="dashboard-skeleton"><Skeleton className="skeleton-controls" /><div className="stat-grid">{Array.from({ length: 4 }, (_, i) => <div key={i} className="card stat-card">{lines(1)}<Skeleton className="skeleton-value" />{lines(1)}</div>)}</div><div className="dashboard-grid dashboard-charts"><div className="card skeleton-panel">{lines(2)}{chart}</div><div className="card skeleton-panel">{lines(2)}<Skeleton className="skeleton-donut" /></div></div><div className="dashboard-grid"><div className="card skeleton-panel">{lines(2)}{rows(3)}</div><div className="card">{rows(3)}</div></div><div className="dashboard-grid">{[0, 1].map(i => <div className="card skeleton-panel" key={i}>{lines(2)}{rows(3)}</div>)}</div></div>
    case 'errors': return <><div className="errors-layout">{[0, 1].map(i => <div className="card skeleton-panel" key={i}>{lines(2)}{rows(5)}</div>)}</div><div className="card">{rows(5)}</div></>
    case 'cards': return <div className="collection-grid">{[0, 1, 2].map(i => <div className="collection-card" key={i}><div className="collection-card-top"><Skeleton className="skeleton-pill" /></div><div>{lines(2)}</div><div className="skeleton-pills"><Skeleton /><Skeleton /></div><div className="collection-card-meta">{lines(2)}</div><div className="collection-card-actions"><Skeleton className="skeleton-pill" /></div></div>)}</div>
    case 'demo': return <div className="case-grid">{[0, 1, 2, 3, 4, 5].map(card)}</div>
    case 'investigations': return <div className="card">{rows(5)}</div>
    case 'collection': return <><div className="skeleton-panel">{lines(2)}<div className="skeleton-pills"><Skeleton /><Skeleton /><Skeleton /></div></div><div className="card skeleton-panel">{lines(2)}<Skeleton className="skeleton-paragraph" />{rows(4)}</div></>
    case 'histogram': return chart
    case 'counts': return <div className="skeleton-counts">{[0, 1, 2].map(i => <Skeleton className="skeleton-line" key={i} />)}</div>
    case 'observations': return <div className="skeleton-table"><div className="skeleton-table-head">{lines(4)}</div>{Array.from({ length: 6 }, (_, i) => <div className="skeleton-table-row" key={i}><Skeleton /><Skeleton className="skeleton-pill" /><Skeleton /><div>{lines(2)}</div></div>)}</div>
    case 'timeline': return <div className="skeleton-timeline">{[0, 1, 2].map(i => <div key={i}><Skeleton className="skeleton-line" /><div className="card skeleton-panel">{lines(3)}</div></div>)}</div>
    case 'flow': return <div className="skeleton-flow"><div className="skeleton-controls">{lines(1)}</div><div className="skeleton-flow-nodes">{[0, 1, 2].map(i => <div className="card skeleton-panel" key={i}>{lines(3)}</div>)}</div>{rows(2)}</div>
    case 'sources': return <div className="source-previews">{Array.from({ length: count }, (_, i) => <div className="skeleton-panel" key={i}>{lines(2)}<Skeleton className="skeleton-paragraph" /></div>)}</div>
    case 'record': return <div className="record-details skeleton-panel">{lines(2)}<div className="skeleton-pills"><Skeleton /><Skeleton /></div><Skeleton className="skeleton-paragraph" />{lines(3)}</div>
    default: return <div className="skeleton-panel">{lines(2)}<Skeleton className="skeleton-paragraph" />{rows(3)}</div>
  }
}
