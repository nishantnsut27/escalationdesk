export type Mappings = { services: Record<string, string>; environments: Record<string, string> }

export function parseAliases(text: string): Record<string, string> {
  const result: Record<string, string> = {}
  for (const line of text.split('\n').filter(line => line.trim())) {
    const split = line.indexOf('=')
    const key = line.slice(0, split).trim(), value = line.slice(split + 1).trim()
    if (split < 1 || !/^(sentry|datadog|manual):.+$/.test(key) || !value || key in result) throw new Error('Use one unique provider:name = shared name mapping per line.')
    result[key] = value
  }
  return result
}

export const formatAliases = (value?: Record<string, string>) => Object.entries(value || {}).map(([key, name]) => `${key} = ${name}`).join('\n')

export function MappingFields({ services, environments, onServices, onEnvironments }: {
  services: string; environments: string; onServices: (value: string) => void; onEnvironments: (value: string) => void
}) {
  return <details><summary>Service and environment aliases (optional)</summary>
    <p>Map names only when you know they refer to the same service or environment. Aliases add context; they do not prove a cause or rewrite the original evidence.</p>
    <div className="observability-fields"><label>Service aliases<textarea value={services} onChange={e => onServices(e.target.value)} placeholder={'sentry:payments-api = payment\ndatadog:pay = payment'} /></label>
      <label>Environment aliases<textarea value={environments} onChange={e => onEnvironments(e.target.value)} placeholder={'sentry:production = prod\ndatadog:prod = prod'} /></label></div>
  </details>
}
