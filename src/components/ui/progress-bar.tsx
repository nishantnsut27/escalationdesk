import { useId, type ReactNode } from 'react'

export type ProgressBarProps = { label: ReactNode; description?: ReactNode; value?: number; isIndeterminate?: boolean }

export function ProgressBar({ label, description, value, isIndeterminate = false }: ProgressBarProps) {
  const id = useId()
  const measured = !isIndeterminate && typeof value === 'number' && Number.isFinite(value)
  const percent = measured ? Math.max(0, Math.min(100, value)) : undefined
  return <div className="progress-bar">
    <div className="progress-label"><span id={id}>{label}</span>{measured && <span>{Math.round(percent!)}%</span>}</div>
    <div className="progress-track" role="progressbar" aria-labelledby={id} aria-describedby={description ? id + '-description' : undefined} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
      <span className={measured ? 'progress-fill' : 'progress-fill progress-indeterminate'} style={measured ? { transform: `scaleX(${percent! / 100})` } : undefined} />
    </div>
    {description && <div id={id + '-description'} className="progress-description">{description}</div>}
  </div>
}
