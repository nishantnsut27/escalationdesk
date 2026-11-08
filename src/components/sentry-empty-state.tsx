import { useId } from 'react'
import { ConnectionIllustration } from './connection-illustration'

export function SentryConnectionEmptyState({ variant = 'empty' }: { variant?: 'empty' | 'setup' | 'projects' }) {
  const titleId = useId()
  const title = variant === 'setup' ? 'Sentry setup is pending' : variant === 'projects' ? 'No authorized projects found' : 'Connect Sentry to bring your evidence together'
  const description = variant === 'setup'
    ? 'Sentry connections are unavailable until the server setup is complete. Your saved snapshots remain readable.'
    : variant === 'projects'
      ? 'Create a project in Sentry, then choose Check access to refresh the projects authorized for this organization.'
      : 'No Sentry connections yet. Authorize your organization, choose your projects, and import a saved time window.'
  return <section className={'sentry-empty-panel' + (variant === 'projects' ? ' sentry-empty-compact' : '')} aria-labelledby={titleId}>
    <ConnectionIllustration /><h2 id={titleId}>{title}</h2><p>{description}</p>
  </section>
}
