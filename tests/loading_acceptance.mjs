// Production UI with controlled slow/failing local responses. No providers or database.
import { preview } from 'vite'
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'

const dir = process.env.DESK_LOADING_ARTIFACTS || '.cache/loading-design/browser'
await mkdir(dir, { recursive: true })
const port = Number(process.env.DESK_LOADING_PORT || 4177)
const server = await preview({ logLevel: 'error', preview: { host: '127.0.0.1', port, strictPort: true } })
const origin = 'http://127.0.0.1:' + port
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
const page = await context.newPage()
const results = [], errors = [], violations = [], requests = [], holds = new Map()
let signedIn = true, empty = false, state = 'completed', stage = 'writing_report', supplementState = null, importState = 'rate_limited'
const user = { id: 'loading-user', name: 'Loading fixture', email: 'loading@tracedesk.test', role: 'regular', read_only: false, email_verified: true }
const stamp = '2026-11-08T10:00:00Z'
const summary = { observations: 2, services: ['checkout'], kinds: ['log'], input_lines: 2, input_bytes: 100, missing_timestamps: 0, missing_services: 0, duplicates: 0, conflicting_ids: 0, malformed_records: 0, redactions: 0, invalid_fields: 0, coverage: 'Synthetic browser fixture.', redaction_note: 'Synthetic content.' }
const collection = { id: 'loading-collection', title: 'Checkout evidence · UI fixture', symptom: 'A saved failure with a long descriptive context.', created_at: stamp, source_type: 'paste', summary }
const rows = [
  { id: 'error-source', collection_id: collection.id, kind: 'log', service: 'checkout', level: 'error', timestamp: stamp, message: 'Database connection pool exhausted during checkout.', error_tags: ['resource_exhaustion', 'database_query', 'long_error_signature_that_must_wrap_without_overflow'] },
  { id: 'info-source', collection_id: collection.id, kind: 'log', service: 'checkout', level: 'info', timestamp: stamp, message: 'Request started.' },
]
const counts = { collections: 1, observations: 2, services: 1, error_logs: 1, missing_timestamps: 0, unknown_time_in_scope: 0, missing_services: 0, correlated: 0, spans: 0, metrics: 0, note: 'Saved evidence.' }
const evidenceSummary = { counts, top_services: [{ service: 'checkout', observations: 1 }], signatures: [{ signature: 'database_query', label: 'Database and queries', observations: 1 }], catalogue: [], investigation_categories: [] }
const histogram = { buckets: [{ start: stamp, end: '2026-11-08T10:01:00Z', count: 2, error: 1, info: 1, trace: 0, debug: 0, warning: 0, unknown: 0 }], total: 2, timed: 2, unknown_timestamps: 0, note: 'Synthetic time bucket.' }
const report = { assessment: 'inconclusive', summary: 'The saved evidence records an exhausted database pool.', origin: 'unknown', affected_services: ['checkout'], evidence_ids: ['error-source'], hypotheses: [], recommendations: ['Inspect the source.'], limitations: ['Synthetic UI fixture.'] }
const investigation = () => ({ id: 'loading-report', title: collection.title, collection_id: collection.id, engine: 'groq', state, stage: state === 'queued' ? null : stage, created_at: stamp, report: state === 'completed' ? report : null, activity: [{ tool: 'read_evidence', count: 2, timestamp: stamp, evidence_ids: ['error-source'] }], usage: { retry_at: new Date(Date.now() + 60000).toISOString() }, supplements: supplementState ? [{ id: 'loading-supplement', investigation_id: 'loading-report', state: supplementState, created_at: stamp, source_ids: ['error-source'], usage: { retry_at: new Date(Date.now() + 60000).toISOString() }, result: null }] : [] })
const logPage = () => ({ collection_id: collection.id, evidence: empty ? [] : rows, total: empty ? 0 : 2, offset: 0, next_offset: null, unknown_timestamps: 0 })
function hold(path) { let resolve; const promise = new Promise(r => { resolve = r }); holds.set(path, { promise, resolve }) }
function release(path, status = 200) { const held = holds.get(path); holds.delete(path); held?.resolve(status) }
async function capture(name, audit = false) {
  await page.evaluate(() => document.fonts.ready)
  await page.waitForFunction(() => [...document.querySelectorAll('.chart-container')].every(chart => {
    const svg = chart.querySelector('svg.recharts-surface')
    return !svg || Math.abs(svg.getBoundingClientRect().width - chart.getBoundingClientRect().width) <= 1
  }))
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) console.log('OVERFLOW ' + name, await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, elements: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed').slice(0, 16).map(e => ({ tag: e.tagName, class: e.className, right: e.getBoundingClientRect().right })) })))
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, name + ': horizontal overflow')
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
  if (audit) {
    const report = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()
    violations.push(...report.violations.map(v => ({ name, id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) })))
  }
}
async function check(name, task) { if (process.env.DESK_LOADING_ONLY && !new RegExp(process.env.DESK_LOADING_ONLY).test(name)) return; await task(); results.push(name); console.log('PASS ' + name) }
page.on('pageerror', e => errors.push(e.message))
await context.route('**/api/**', async route => {
  const request = route.request(), path = new URL(request.url()).pathname
  requests.push({ path, method: request.method() })
  let status = holds.has(path) ? await holds.get(path).promise : 200, body
  if (path === '/api/auth/me') { if (!signedIn) status = 401; body = { user, csrf: 'fixture' } }
  else if (path === '/api/auth/login') { signedIn = true; body = { user, csrf: 'fixture' } }
  else if (path === '/api/health') body = { demo_available: true, demo_case_count: 2 }
  else if (path === '/api/dashboard') body = { ...evidenceSummary, collections: [collection], trend: histogram, recent_investigations: [investigation()], last_import: stamp, connections: [], monitoring_note: 'Saved snapshots.' }
  else if (path === '/api/errors') body = evidenceSummary
  else if (path === '/api/collections') body = empty ? [] : [collection]
  else if (path === '/api/demo/cases') body = [{ slug: 'checkout', title: 'Recorded checkout failure', description: 'Synthetic UI fixture.', category: 'Database', recorded_at: stamp, collection_id: collection.id, investigation_id: 'loading-report', counts: { log: 2, span: 0, metric: 0 } }]
  else if (path === '/api/investigations') body = [investigation()]
  else if (path === '/api/collections/loading-collection') body = { collection, investigations: [investigation()] }
  else if (path.endsWith('/analysis')) body = { ...evidenceSummary, collection, run: null, services: ['checkout'], traces: [], requests: [] }
  else if (path.endsWith('/histogram')) body = histogram
  else if (path.endsWith('/logs') || path.endsWith('/timeline') || path === '/api/logs') body = logPage()
  else if (path.endsWith('/flow')) body = { mode: 'events', nodes: [], edges: [], events: rows, total: 2, scope: 'Synthetic fixture', note: 'No span relationships supplied.' }
  else if (path === '/api/investigations/loading-report/evidence') body = { investigation_id: 'loading-report', evidence: rows, source_ids: ['error-source'], unavailable_source_ids: [], coverage: 'saved', note: 'Synthetic evidence.' }
  else if (path === '/api/investigations/loading-report') body = investigation()
  else if (path.startsWith('/api/evidence/')) body = rows.find(row => path.endsWith(row.id))
  else if (path === '/api/imports') body = collection
  else if (path === '/api/connections/sentry') body = { configured: false, public_url: '', connections: [], imports: [{ id: 'loading-import', connection_id: 'fixture', collection_id: collection.id, state: importState, created_at: stamp, completed_at: null, error: importState === 'failed' ? 'Import failed. Retry explicitly.' : null, parameters: { title: 'Checkout import' }, progress: { observations: 24, message: 'Reading authorized log pages', retry_at: Math.floor(Date.now() / 1000) + 60 } }] }
  else { status = 404; body = { error: 'Unknown fixture request' } }
  if (status >= 400) body = { error: 'The fixture request failed. Try again.' }
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {})
})

try {
  await check('centered initialization cup reserves space', async () => {
    await page.setViewportSize({ width: 390, height: 1000 })
    hold('/api/auth/me'); await page.goto(origin + '/#/dashboard'); await page.locator('.cup-loader').waitFor()
    await capture('initializing-390', true)
    release('/api/auth/me'); await page.getByRole('heading', { name: 'Evidence overview' }).waitFor()
    await page.goto(origin + '/#/privacy')
  })
  await check('matching skeletons fit all five viewport widths', async () => {
    for (const width of [320, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 1000 })
      for (const [path, route, selector, name] of [
        ['/api/dashboard', '/dashboard', '.dashboard-skeleton', 'dashboard'],
        ['/api/collections', '/collections', '.layout-skeleton .collection-card', 'collections'],
        ['/api/errors', '/errors', '.layout-skeleton .errors-layout', 'errors'],
        ['/api/demo/cases', '/demo', '.layout-skeleton .case-grid', 'demo'],
        ['/api/investigations', '/investigations', '.layout-skeleton .skeleton-row', 'investigations'],
      ]) {
        hold(path); await page.goto(origin + '/#' + route); await page.locator(selector).first().waitFor()
        await capture(`${name}-pending-${width}`, width === 390)
        release(path); await page.locator('.layout-skeleton').waitFor({ state: 'detached' })
      }
    }
  })
  await check('logs sections load independently, fail visibly, and retry', async () => {
    const base = '/api/collections/loading-collection'
    for (const suffix of ['/analysis', '/histogram', '/logs', '/timeline', '/flow']) hold(base + suffix)
    hold('/api/evidence/error-source')
    await page.goto(origin + '/#/logs?collection=loading-collection&flow=1&timeline=1&evidence=error-source')
    await page.locator('.skeleton-table').waitFor()
    for (const width of [320, 390, 768, 1024, 1440]) { await page.setViewportSize({ width, height: 1000 }); await capture(`logs-pending-${width}`, width === 390) }
    release(base + '/logs'); await page.locator('.log-table tbody tr').first().waitFor()
    assert(await page.locator('.skeleton-chart').isVisible()); assert(await page.locator('.skeleton-flow').isVisible())
    release(base + '/histogram', 503); await page.locator('.panel').filter({ has: page.getByRole('heading', { name: 'Log volume', exact: true }) }).getByRole('alert').waitFor()
    assert.equal(await page.locator('.skeleton-chart').count(), 0); assert.equal(await page.locator('.log-table tbody tr').count(), 2)
    release(base + '/analysis'); release(base + '/timeline'); release(base + '/flow'); release('/api/evidence/error-source')
    await page.locator('.record-details').waitFor(); await page.locator('.skeleton-flow').waitFor({ state: 'detached' })
    await capture('logs-partial-failure', true)
    await page.locator('.panel').filter({ has: page.getByRole('heading', { name: 'Log volume', exact: true }) }).getByRole('button', { name: 'Try again' }).click()
    await page.locator('.histogram').waitFor(); await page.locator('.layout-skeleton').waitFor({ state: 'detached' })
    for (const width of [320, 390, 768, 1024, 1440]) { await page.setViewportSize({ width, height: 1000 }); await capture(`logs-loaded-${width}`, width === 390) }
  })
  await check('pending catalogue rows never masquerade as empty; failures stop shimmer', async () => {
    hold('/api/logs'); await page.goto(origin + '/#/errors'); await page.locator('.skeleton-table').waitFor()
    assert.equal(await page.getByRole('heading', { name: 'No matching log observations' }).count(), 0)
    release('/api/logs', 503); await page.getByRole('alert').waitFor(); assert.equal(await page.locator('.skeleton-table').count(), 0)
    await capture('errors-failed')
    await page.getByRole('button', { name: 'Try again' }).click(); await page.locator('.error-observations').waitFor()
    empty = true; await page.goto(origin + '/#/collections'); await page.getByRole('heading', { name: 'No evidence saved yet' }).waitFor()
    assert.equal(await page.locator('.skeleton').count(), 0)
    await page.goto(origin + '/#/logs?collection=loading-collection'); await page.getByRole('heading', { name: 'No observations match' }).waitFor(); empty = false
  })
  await check('queued, running, capacity wait, completed and failed analysis use truthful progress', async () => {
    state = 'queued'; await page.goto(origin + '/#/investigations/loading-report'); await page.getByRole('heading', { name: 'Investigation queued' }).waitFor()
    assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuenow'), null)
    for (const width of [320, 390, 768, 1024, 1440]) { await page.setViewportSize({ width, height: 1000 }); await capture(`analysis-queued-${width}`, width === 390) }
    state = 'running'; await page.getByRole('heading', { name: 'Writing the assessment' }).waitFor(); assert.equal(await page.locator('.cup-loader').count(), 0)
    await capture('analysis-running')
    state = 'quota_waiting'; await page.getByRole('heading', { name: 'Waiting for analysis capacity' }).waitFor(); assert(await page.locator('.cup-loader').isVisible()); await capture('analysis-capacity-wait')
    state = 'completed'; hold('/api/investigations/loading-report/evidence'); await page.getByRole('heading', { name: 'Assessed cause' }).waitFor(); await page.locator('.source-previews .skeleton').first().waitFor(); await capture('source-previews-pending')
    release('/api/investigations/loading-report/evidence'); await page.locator('.source-previews > a').first().waitFor(); assert.equal(await page.getByRole('progressbar').count(), 0)
    supplementState = 'quota_waiting'; await page.reload(); await page.locator('.supplement-progress').waitFor(); await capture('further-analysis-waiting', true)
    supplementState = 'running'; await page.locator('.supplement-progress .progress-label').filter({ hasText: 'Reviewing the saved assessment' }).waitFor(); assert.equal(await page.locator('.supplement-progress .cup-loader').count(), 0)
    supplementState = 'failed'; await page.getByRole('button', { name: 'Retry further analysis', exact: true }).waitFor(); assert.equal(await page.locator('.supplement-progress').count(), 0)
    state = 'failed'; supplementState = null; await page.reload(); await page.getByRole('heading', { name: 'This investigation did not finish' }).waitFor(); assert.equal(await page.getByRole('progressbar').count(), 0)
    await capture('analysis-failed')
    state = 'completed'
  })
  await check('Sentry import waits show cup, counts and retry time; polling preserves content', async () => {
    await page.goto(origin + '/#/connections'); await page.getByRole('progressbar').waitFor()
    assert(await page.getByText('Next retry:').isVisible()); assert(await page.locator('.cup-loader').isVisible()); await capture('import-rate-limited', true)
    hold('/api/connections/sentry'); await page.waitForTimeout(4200)
    assert(await page.getByText('Checkout import', { exact: true }).isVisible()); assert.equal(await page.locator('.layout-skeleton').count(), 0)
    importState = 'running'; release('/api/connections/sentry'); await page.getByText('Importing evidence', { exact: true }).waitFor(); assert.equal(await page.locator('.cup-loader').count(), 0)
    await capture('import-running')
    importState = 'failed'; await page.getByRole('button', { name: 'Retry as new snapshot' }).waitFor(); assert.equal(await page.getByRole('progressbar').count(), 0)
  })
  await check('short native requests retain labels, dimensions, keyboard activation and disabled depth', async () => {
    signedIn = false; await page.goto(origin + '/#/login'); await page.reload(); const button = page.getByRole('button', { name: 'Sign in', exact: true }); await button.waitFor()
    await page.getByLabel('Email address').fill(user.email); await page.getByLabel('Password', { exact: true }).fill('UI-fixture-password')
    await capture('button-resting')
    await button.screenshot({ path: `${dir}/button-resting-detail.png` })
    await page.emulateMedia({ reducedMotion: 'no-preference' }); await button.hover()
    const before = await button.boundingBox(); assert.equal(await button.evaluate(e => getComputedStyle(e).boxShadow), 'none')
    await capture('button-hover'); await button.screenshot({ path: `${dir}/button-hover-detail.png` })
    await button.focus(); assert.equal(await button.evaluate(e => getComputedStyle(e).outlineColor), 'rgb(49, 203, 83)')
    await page.keyboard.down('Space'); await page.waitForTimeout(120); assert.equal(await button.evaluate(e => getComputedStyle(e).transform), 'matrix(1, 0, 0, 1, 0, 2)')
    await capture('button-pressed'); await button.screenshot({ path: `${dir}/button-pressed-detail.png` })
    hold('/api/auth/login'); const writes = requests.filter(r => r.method === 'POST').length; await page.keyboard.up('Space'); await page.locator('button[aria-busy=true]').waitFor()
    assert(await button.isDisabled()); assert.deepEqual(await button.boundingBox(), before); assert.equal(await button.evaluate(e => getComputedStyle(e).transform), 'none'); await capture('auth-busy', true)
    assert.equal(await button.evaluate(e => getComputedStyle(e).boxShadow), 'none'); await button.screenshot({ path: `${dir}/button-loading-detail.png` })
    await page.keyboard.press('Enter'); assert.equal(requests.filter(r => r.method === 'POST').length, writes + 1)
    release('/api/auth/login', 503); await page.getByRole('alert').waitFor(); assert(await button.isEnabled()); assert.equal(await button.locator('.spin').count(), 0)
    signedIn = true; await page.goto(origin + '/#/import'); await page.reload(); await page.getByRole('button', { name: 'Save logs', exact: true }).waitFor()
    const disabled = page.getByRole('button', { name: 'Save logs', exact: true }); assert(await disabled.isDisabled()); assert.equal(await disabled.evaluate(e => getComputedStyle(e).boxShadow), 'none')
    await disabled.screenshot({ path: `${dir}/button-disabled-detail.png` })
    await page.getByLabel('Log content').fill('UI fixture log text')
    const save = page.getByRole('button', { name: 'Save logs', exact: true }), size = await save.boundingBox(); hold('/api/imports'); await save.click(); await page.getByRole('progressbar').waitFor(); assert.deepEqual(await save.boundingBox(), size); await capture('import-active-390')
    release('/api/imports', 503); await page.getByRole('alert').waitFor(); assert.equal(await page.getByRole('progressbar').count(), 0)
    await page.goto(origin + '/#/dashboard'); const importLogs = page.locator('#main-content').getByRole('link', { name: 'Import logs', exact: true }); await importLogs.waitFor()
    await importLogs.screenshot({ path: `${dir}/import-logs-resting-detail.png` }); await importLogs.hover()
    assert.equal(await importLogs.evaluate(e => getComputedStyle(e).boxShadow), 'none'); await importLogs.screenshot({ path: `${dir}/import-logs-hover-detail.png` })
    await page.goto(origin + '/#/logs?collection=loading-collection'); await page.locator('.log-table tbody tr').first().waitFor()
    const copy = page.getByRole('button', { name: 'Copy view link', exact: true }); await copy.hover(); assert.equal(await copy.evaluate(e => getComputedStyle(e).boxShadow), 'none')
    await copy.screenshot({ path: `${dir}/secondary-copy-detail.png` })
    const previous = page.getByRole('button', { name: 'Previous', exact: true }); assert(await previous.isDisabled()); assert.equal(await previous.evaluate(e => getComputedStyle(e).boxShadow), 'none')
    await previous.screenshot({ path: `${dir}/secondary-disabled-detail.png` })
  })
  await check('reduced motion and hidden tabs pause every repeating loader', async () => {
    hold('/api/dashboard'); await page.goto(origin + '/#/dashboard'); await page.locator('.skeleton').first().waitFor()
    await page.emulateMedia({ reducedMotion: 'no-preference' }); assert.equal(await page.locator('.skeleton').first().evaluate(e => getComputedStyle(e, '::after').animationDuration), '1.5s')
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')) })
    assert.equal(await page.locator('.skeleton').first().evaluate(e => getComputedStyle(e, '::after').animationPlayState), 'paused')
    await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')) })
    await page.emulateMedia({ reducedMotion: 'reduce' }); assert.equal(await page.locator('.skeleton').first().evaluate(e => getComputedStyle(e, '::after').animationName), 'none'); release('/api/dashboard')
    state = 'queued'; await page.goto(origin + '/#/investigations/loading-report'); await page.locator('.cup-loader').waitFor()
    for (const selector of ['.cup-vessel', '.cup-steam span', '.progress-indeterminate']) assert.equal(await page.locator(selector).first().evaluate(e => getComputedStyle(e).animationName), 'none')
    await page.emulateMedia({ reducedMotion: 'no-preference' }); assert.equal(await page.locator('.cup-vessel').evaluate(e => getComputedStyle(e).animationDuration), '6s')
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')) })
    for (const selector of ['.cup-vessel', '.cup-steam span', '.progress-indeterminate']) assert.equal(await page.locator(selector).first().evaluate(e => getComputedStyle(e).animationPlayState), 'paused')
  })
  assert.deepEqual(errors, []); assert.deepEqual(violations, [])
} catch (error) { errors.push(error.stack); console.error(error); process.exitCode = 1; await page.screenshot({ path: `${dir}/failure.png`, fullPage: true }).catch(() => {}) }
finally {
  for (const path of holds.keys()) release(path)
  await writeFile(`${dir}/acceptance.json`, JSON.stringify({ results, errors, violations, requests, backend: 'Controlled local fixtures only' }, null, 2))
  await browser.close(); await new Promise(resolve => server.httpServer.close(resolve))
}
