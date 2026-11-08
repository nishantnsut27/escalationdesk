import { Route } from 'lucide-react'

// Layered-card composition from the user's reference; symbol: lucide.dev/icons/route.
export function ConnectionIllustration() {
  return <svg className="connection-illustration" aria-hidden="true" focusable="false" width={140} height={156} viewBox="0 0 140 156" fill="none">
    {[{ x: 14, y: 44, front: false }, { x: 33, y: 52, front: false }, { x: 52, y: 60, front: true }].map(({ x, y, front }) => <g key={x} transform={`translate(${x} ${y}) skewY(-28)`}>
      <rect x={-4} y={-2} width={60} height={80} rx={9} className="connection-card-edge" stroke="currentColor" strokeWidth={0.6} />
      <rect width={60} height={80} rx={9} className="connection-card-face" stroke="currentColor" strokeWidth={front ? 1.2 : 0.6} />
      {front && <Route x={14} y={23} width={32} height={32} strokeWidth={1.8} />}
    </g>)}
  </svg>
}
