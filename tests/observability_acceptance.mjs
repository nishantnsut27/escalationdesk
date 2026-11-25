// Production UI with synthetic, owned API fixtures. No database or provider calls.
import { preview } from 'vite'
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'

const dir = '.cache/next-phase/browser/'+Date.now(), origin = 'http://127.0.0.1:4187'
await mkdir(dir, { recursive: true })
const server = await preview({ logLevel: 'error', preview: { host: '127.0.0.1', port: 4187, strictPort: true } })
let browser
const errors = [], violations = [], results = [], writes = [], unexpected = []
const stamp = '2026-11-10T10:00:00Z'
const sentry = { id: 's', provider: 'sentry', organization: 'Fixture Sentry', region: 'us', state: 'connected', created_at: stamp, checked_at: stamp, projects: [{ id: '42', name: 'Payment', slug: 'payment' }], capabilities: { logs: { state: 'available' } } }
const datadog = { ...sentry, id: 'd', provider: 'datadog', organization: 'Fixture Datadog', region: 'eu1', projects: [], capabilities: { logs: { state: 'available' }, spans: { state: 'available' }, metrics: { state: 'unavailable', reason: 'Metric scope is not granted.' }, monitors: { state: 'available' } } }
let connections = [sentry, datadog]
let settings = { enabled: false, initial_email: true, result_email: false, webhook_configured: true, webhook_url: `${origin}/api/connections/datadog/d/webhook`, configuration: { monitor_ids: ['12'], metric_queries: [], enrichment: null, mappings: {} } }
const summary = { observations: 2, services: ['payment'], kinds: ['log'], input_bytes: 120, input_lines: 2, duplicates: 0, conflicting_ids: 0, malformed_records: 0, redactions: 0, missing_timestamps: 0, missing_services: 0, invalid_fields: 0, coverage: 'Synthetic browser fixture.', redaction_note: 'Sanitized fixture.' }
const collections = ['one', 'two', 'combined'].map((id, i) => ({ id, title: i === 2 ? 'Combined evidence' : `Snapshot ${i+1}`, symptom: '', filename: null, source_type: i === 2 ? 'composite' : i === 1 ? 'datadog' : 'sentry', created_at: stamp, summary }))
const hypothesis = { id: 'h1', description: 'Database connection pressure', status: 'unresolved', support_level: 'tentative', supporting_evidence_ids: ['a'], contradicting_evidence_ids: ['b'], missing_evidence: ['Pool capacity'], transition_reason: 'Some requests succeeded; failure scope remains uncertain.' }
const report = { id: 'report', engine: 'groq', state: 'completed', collection_id: 'combined', created_at: stamp, completed_at: stamp, activity: [], supplements: [], usage: { workflow_version: 'v2', report_format: 'concise-v1', hypotheses: [hypothesis], requests: 2, total_tokens: 200 }, report: { assessment: 'inconclusive', summary: 'Observed acquisition failures; internal cause is not established.', origin: 'unknown', affected_services: ['payment'], evidence_ids: ['a'], supporting_evidence: [{ finding: 'Acquisition failed.', evidence_ids: ['a'] }], hypotheses: [], limitations: ['No pool measurements.'], recommendations: ['Collect pool measurements.'] } }
try {
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
  context.on('page', page => page.on('pageerror', e => errors.push(e.message)))
  await context.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method()
    let body, status = 200
    if (method !== 'GET') writes.push({ path, body: request.postDataJSON() })
    if (path === '/api/auth/me') body = { user: { id: 'owner', name: 'Fixture User', email: 'owner@example.invalid', role: 'regular', read_only: false, email_verified: true }, csrf: 'fixture' }
    else if (path === '/api/health') body = { demo_available: true, demo_case_count: 6 }
    else if (path === '/api/connections/sentry') body = { configured: true, public_url: '', connections: [sentry], imports: [] }
    else if (path === '/api/connections') body = { datadog_enabled: true, connections, imports: [] }
    else if (path === '/api/connections/sentry/s/automation') body = { enabled: false, projects: [], initial_email: false, result_email: false }
    else if (path === '/api/connections/datadog/d/automation') {
      if (method === 'PUT') {
        const value = request.postDataJSON()
        settings = { ...settings, enabled: value.enabled, initial_email: value.initial_email, result_email: value.result_email,
          configuration: { monitor_ids: value.monitor_ids, metric_queries: value.metric_queries, enrichment: value.enrichment, mappings: value.mappings } }
      }
      body = settings
    } else if (path === '/api/connections/datadog/d' && method === 'DELETE') { connections = [sentry]; body = { ok: true } }
    else if (path === '/api/connections/datadog/d/check') { status = 400; body = { error: 'Datadog denied this capability; check application-key scopes.' } }
    else if (path === '/api/collections') body = collections.slice(0, 2)
    else if (path === '/api/collections/composite') { status = 201; body = { id: 'combined' } }
    else if (path === '/api/collections/combined') body = { collection: collections[2], investigations: [report] }
    else if (path === '/api/collections/combined/relationships') body = { edges: [{ id: 'link', source: 'a', target: 'b', relationship: 'shared_cross_source_trace', support: 'established', justification: { full_128_bit_identity: 'a'.repeat(32), environment: 'test' } }], ambiguities: [], omitted_edges: 0, note: 'Recorded identity does not establish causality.' }
    else if (path === '/api/investigations/report') body = report
    else if (path === '/api/investigations/report/evidence') body = { investigation_id: 'report', evidence: [], source_ids: ['a', 'b'], unavailable_source_ids: [], coverage: 'recorded_supplied', note: 'Fixture' }
    else if (path === '/api/investigations/report/replay') body = { recorded: true, integrity: 'verified', checkpoint_stage: 'completed', uncertain_requests: [], note: 'Recorded playback; not a new investigation.', events: [{ sequence: 1, kind: 'hypotheses_updated', created_at: stamp, payload: { hypotheses: [hypothesis] } }] }
    else { status = 404; body = { error: 'Fixture route unavailable.' }; unexpected.push(path) }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
  const page = await context.newPage()
  async function capture(name) {
    await page.evaluate(() => document.fonts.ready)
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1), `${name}: horizontal overflow`)
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
    const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()
    violations.push(...result.violations.map(v => ({ name, id: v.id, nodes: v.nodes.map(n => n.target) })))
  }
  await page.goto(origin+'/connections')
  await page.getByRole('region', { name: 'Sentry settings' }).waitFor()
  assert.equal(await page.getByRole('heading', { name: 'Datadog', exact: true }).count(), 0)
  await page.getByLabel('Integration', { exact: true }).selectOption('datadog')
  await page.getByRole('heading', { name: 'Datadog', exact: true }).waitFor()
  assert.equal(await page.getByRole('region', { name: 'Sentry settings' }).count(), 0)
  await page.getByText('Monitor-triggered investigations', { exact: true }).click()
  await page.getByLabel('Monitor IDs (comma separated)').waitFor()
  assert.equal(await page.getByLabel('Monitor IDs (comma separated)').inputValue(), '12')
  assert.equal(writes.length, 0)
  await page.getByLabel('Enable selected monitors').check()
  await page.getByLabel('Sentry enrichment').selectOption('s')
  await page.getByRole('group', { name: 'Enrichment projects' }).getByLabel('Payment').check()
  await page.getByText('Service and environment aliases (optional)', { exact: true }).click()
  await page.getByLabel('Service aliases', { exact: true }).fill('sentry:payments = payment\ndatadog:payment = payment')
  await page.getByRole('button', { name: 'Save monitor settings' }).click()
  await page.getByText('Monitor automation saved.', { exact: true }).waitFor()
  assert.deepEqual(settings.configuration.enrichment, { connection_id: 's', project_ids: ['42'] })
  await page.reload()
  await page.getByText('Monitor-triggered investigations', { exact: true }).click()
  await page.getByLabel('Enable selected monitors').waitFor()
  assert(await page.getByLabel('Enable selected monitors').isChecked())
  assert.equal(await page.getByLabel('Sentry enrichment').inputValue(), 's')
  assert.equal(await page.getByLabel('Integration', { exact: true }).inputValue(), 'datadog')
  await page.getByLabel('Integration', { exact: true }).selectOption('sentry')
  await page.getByRole('region', { name: 'Sentry settings' }).waitFor()
  await page.goBack()
  await page.getByRole('region', { name: 'Datadog settings' }).waitFor()
  await page.getByText('Monitor-triggered investigations', { exact: true }).click()
  results.push('One settings area with provider dropdown; URL, reload and Back preserve integration choice')
  results.push('Saved monitor settings, enrichment and mappings survive reload; reads cause no writes')
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await capture(`connections-${width}`)
  }
  await page.locator('section[aria-labelledby="datadog-title"]').getByRole('button', { name: 'Check access', exact: true }).click()
  await page.getByText('Datadog denied this capability; check application-key scopes.', { exact: true }).waitFor()
  results.push('Permission failure remains visible without removing saved settings')
  await page.goto(origin+'/collections')
  await page.getByText('Combine saved snapshots', { exact: true }).click()
  await page.getByLabel('Snapshot 1 (sentry)').check(); await page.getByLabel('Snapshot 2 (datadog)').check()
  await page.getByRole('button', { name: 'Create combined snapshot' }).click()
  await page.getByRole('heading', { name: 'Combined evidence', exact: true }).waitFor()
  await page.getByText('Evidence relationships', { exact: true }).click()
  await page.getByText('Relationship justification', { exact: true }).click()
  await capture('combined-mobile')
  assert(await page.getByText('shared cross source trace', { exact: false }).isVisible())
  results.push('Composite creation opens saved report and justified cross-source links')
  await page.goto(origin+'/investigations/report')
  await page.getByRole('heading', { name: 'Hypothesis tracking' }).waitFor()
  await page.getByText('Recorded investigation steps', { exact: true }).click()
  await page.getByText('Recording integrity: verified.', { exact: false }).waitFor()
  await page.getByRole('link', { name: 'Download recorded execution' }).waitFor()
  await capture('recording-mobile')
  await page.setViewportSize({ width: 1440, height: 1000 }); await capture('recording-desktop')
  assert.equal(writes.filter(r => /investigations|supplement/.test(r.path)).length, 0)
  results.push('Hypothesis support/contradiction citations and verified replay; browsing never starts analysis')
  assert.deepEqual(errors, []); assert.deepEqual(violations, []); assert.deepEqual(unexpected, [])
  console.log(JSON.stringify({ results, violations, errors }))
} catch (e) { errors.push(e.stack); console.error(e); process.exitCode = 1 }
finally {
  await writeFile(`${dir}/acceptance.json`, JSON.stringify({ results, errors, violations, writes, unexpected, backend: 'mocked; no database or external providers' }, null, 2))
  await browser?.close(); await new Promise(resolve => server.httpServer.close(resolve))
}
