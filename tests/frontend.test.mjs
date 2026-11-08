import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
import { readFileSync } from 'node:fs'
import { build } from 'esbuild'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createRequire } from 'node:module'

async function components(entry) {
  const bundle = await build({ entryPoints: [entry], bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external' })
  const output = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), output, output.exports)
  return output.exports
}

const reportFixture = { assessment: 'inconclusive', origin: 'payment', summary: 'The source is uncertain.', affected_services: ['order'], evidence_ids: ['source-a'],
  hypotheses: [{ claim: 'Processing may be delayed.', evidence_ids: ['source-b'] }], recommendations: ['Inspect linked spans.'], limitations: ['Deeper cause unknown.'] }
const investigationFixture = { id: 'saved', engine: 'groq', state: 'completed', created_at: '2026-11-02T00:00:05Z', completed_at: '2026-11-02T00:00:09Z', report: reportFixture, activity: [], usage: { requests: 2, total_tokens: 200 }, supplements: [] }

test('durable routes retain evidence filters and only restore internal destinations after authentication', async () => {
  const { parseRoute, destinationAfterAuth, evidenceFilters, logHref } = await components('src/routes.ts')
  const link = logHref({ collection: 'saved collection', evidence: 'source-a', investigation: 'report-a', query: 'Caused by:', level: 'error' })
  const route = parseRoute(link)
  assert.equal(route.page, 'logs')
  assert.equal(route.params.get('collection'), 'saved collection')
  assert.equal(route.params.get('evidence'), 'source-a')
  assert.equal(route.params.get('correlate'), '1')
  assert.equal(evidenceFilters(route.params).get('query'), 'Caused by:')
  assert.equal(evidenceFilters(route.params).has('investigation'), false)
  assert.equal(destinationAfterAuth(link), link)
  assert.equal(destinationAfterAuth('#/login', link), link)
  assert.equal(destinationAfterAuth('#/login', 'https://example.test'), '#/dashboard')
  assert.equal(destinationAfterAuth('#/demo-login', '', true), '#/demo')
  assert.equal(parseRoute('#overview').page, 'dashboard')
  assert.equal(parseRoute('#auth/verify/private-token').page, 'auth-link')
  assert.equal(parseRoute('#/demo/recorded-case').id, 'recorded-case')
})

test('citation links prefer a known trace and never invent correlation for an uncorrelated observation', async () => {
  const { logHref, parseRoute } = await components('src/routes.ts')
  const row = { id: 'cited', kind: 'log', service: 'billing', timestamp: null, trace_id: 'trace-recorded', request_id: 'request-recorded' }
  const traced = parseRoute(logHref({ row, request_id: 'stale-request' })).params
  assert.equal(traced.get('trace_id'), 'trace-recorded')
  assert.equal(traced.has('request_id'), false)
  const uncorrelated = parseRoute(logHref({ row: { ...row, trace_id: undefined, request_id: undefined }, trace_id: 'unrelated' })).params
  assert.equal(uncorrelated.has('trace_id'), false)
  assert.equal(uncorrelated.has('request_id'), false)
  assert.equal(uncorrelated.get('evidence'), 'cited')
})

test('source timestamps require a timezone and never use a browser-invented date', async () => {
  const { sourceDate } = await components('src/lib/time.ts')
  for (const invalid of [null, '', '2026-11-07', '2026-11-07T10:00:00', '2026-02-30T10:00:00Z', 'invalid']) assert.equal(sourceDate(invalid), null)
  assert.equal(sourceDate('2026-11-07T15:30:00+05:30').toISOString(), '2026-11-07T10:00:00.000Z')
  const { orderedEvidence, FullTime } = await components('src/reports.tsx')
  assert.match(renderToStaticMarkup(createElement(FullTime, { value: '2026-11-07T10:00:00' })), /timezone missing or invalid/)
  assert.deepEqual(orderedEvidence([{ id: 'naive', kind: 'log', timestamp: '2020-01-01T00:00:00' }, { id: 'known', kind: 'log', timestamp: '2026-11-07T00:00:00Z' }]).map(row => row.id), ['known', 'naive'])
})

test('authentication links require explicit action and keep tokens out of rendered markup', async () => {
  const { AccountPage, parseAuthenticationLink } = await components('src/account.tsx')
  const token = 'token_'.repeat(7)
  assert.deepEqual(parseAuthenticationLink('#auth/verify/' + token), { purpose: 'verify', token })
  for (const invalid of ['#auth/verify/short', '#auth/reset/' + token + '?query=1', '#auth/other/' + token]) assert.equal(parseAuthenticationLink(invalid), null)
  const html = renderToStaticMarkup(createElement(AccountPage, { link: { purpose: 'verify', token }, onExit() {} }))
  assert.match(html, /Confirm email address/)
  assert.doesNotMatch(html, new RegExp(token))
  assert.match(renderToStaticMarkup(createElement(AccountPage, { invalid: true, onExit() {} })), /This link is invalid/)
})

test('verification banner preserves history guidance and stays hidden for demo accounts', async () => {
  const { VerificationBanner } = await components('src/account.tsx')
  const user = { id: 'regular', email: 'owner@example.test', name: 'Owner', email_verified: false, read_only: false }
  const html = renderToStaticMarkup(createElement(VerificationBanner, { user, onRefresh() {} }))
  assert.match(html, /Your saved history is retained/)
  assert.match(html, /Send verification email/)
  assert.equal(renderToStaticMarkup(createElement(VerificationBanner, { user: { ...user, read_only: true }, onRefresh() {} })), '')
})

test('account access offers password visibility, registration and a dedicated verification screen', async () => {
  const { PasswordField, AccountPage, VerifyAccountPage } = await components('src/account.tsx')
  const password = renderToStaticMarkup(createElement(PasswordField, { label: 'New password', name: 'password' }))
  assert.match(password, /type="password"/)
  assert.match(password, /aria-label="Show new password"/)
  assert.match(password, /type="button"/)
  const recovery = renderToStaticMarkup(createElement(AccountPage, { onExit() {}, onRegister() {} }))
  assert.match(recovery, /Create account/)
  assert.match(recovery, /Password recovery is for existing accounts/)
  const verification = renderToStaticMarkup(createElement(VerifyAccountPage, {
    user: { email: 'owner@example.test', email_verified: false, read_only: false }, onRefresh() {}, onLogout() {} }))
  assert.match(verification, /Verify your email to continue/)
  assert.match(verification, /Send verification email/)
  assert.doesNotMatch(verification, /class="app-shell"/)
})

test('demo reports expose saved sources without supplement creation', async () => {
  const { ReportView } = await components('src/assessments.tsx')
  const html = renderToStaticMarkup(createElement(ReportView, { investigation: investigationFixture, readOnly: true, onOpen() {} }))
  assert.match(html, /Open log workspace/)
  assert.match(html, /Reported hypotheses/)
  assert.doesNotMatch(html, /Analyze further|Retry further analysis/)
})

test('demo cards show the supplied published count and durable links', async () => {
  const { CaseCards } = await components('src/scenarios.tsx')
  const cases = Array.from({ length: 6 }, (_, i) => ({ slug: String(i), title: 'Recorded case ' + i, description: 'Controlled execution', category: 'Deployment', recorded_at: '2026-11-03T00:00:00Z', counts: { log: 501, span: 40, metric: 12 } }))
  const html = renderToStaticMarkup(createElement(CaseCards, { cases, onSelect() {} }))
  assert.equal((html.match(/class="case-card"/g) || []).length, 6)
  assert.equal((html.match(/501 logs/g) || []).length, 6)
  assert.doesNotMatch(html, /Run scenario|Investigate|Analyze further/)
})

test('Jev triage stays advisory and separate from the assessed cause and Groq usage', async () => {
  const { ReportView } = await components('src/assessments.tsx')
  const triage = { requested: true, state: 'completed', category: 'resource_exhaustion', confidence: .6,
    probabilities: { resource_exhaustion: .7, latency_timeout: .3 }, requests: 1,
    returned_model: 'typesafe/jev-1.14', actual_usage: { input_tokens: 123, output_tokens: 45 },
    groups: [{ id: 'g0', source_ids: ['source-a'], score: 1.5 }] }
  const investigation = { ...investigationFixture, usage: { ...investigationFixture.usage, triage } }
  const html = renderToStaticMarkup(createElement(ReportView, { investigation, onOpen() {} }))
  assert.match(html, /inconclusive/)
  assert.match(html, /Evidence review · Resource exhaustion/)
  assert.match(html, /not a root-cause finding/)
  assert.match(html, /The assessment can disagree/)
  assert.doesNotMatch(html, /Input tokens|HTTP attempts|Priority 1.50/)
  assert.doesNotMatch(html, /<details[^>]*open=/)
})

test('triage pending, fallback, unknown and missing usage have truthful display', async () => {
  const { TriagePanel } = await components('src/triage.tsx')
  for (const state of ['pending', 'running', 'fallback', 'interrupted']) {
    const html = renderToStaticMarkup(createElement(TriagePanel, { triage: { requested: true, state, reason: 'Provider unavailable' }, onOpen() {} }))
    assert.match(html, state === 'fallback' ? /Groq-only fallback/ : /Evidence review/)
    assert.doesNotMatch(html, /Assessed cause/)
  }
  const html = renderToStaticMarkup(createElement(TriagePanel, { triage: { requested: true, state: 'completed', category: 'no_clear_failure', probabilities: {}, groups: [] }, onOpen() {} }))
  assert.match(html, /does not establish application health/)
  assert.doesNotMatch(html, /Input tokens/)
})

test('source previews show actual observations, missing timestamps and unavailable sources', async () => {
  const { SourcePreviews } = await components('src/triage.tsx')
  const scope = { evidence: [{ id: 'a', service: 'catalog', kind: 'log', message: 'Actual TypeError', timestamp: null }] }
  const html = renderToStaticMarkup(createElement(SourcePreviews, { ids: ['a', 'a', 'missing'], scope, onOpen() {} }))
  assert.match(html, /Actual TypeError/)
  assert.match(html, /Timestamp not recorded/)
  assert.match(html, /Saved source unavailable/)
  assert.equal((html.match(/Open evidence a/g) || []).length, 1)
})

test('saved reports expose individual hypotheses, neutral assessment, timeline and explicit further analysis', async () => {
  const { ReportView } = await components('src/assessments.tsx')
  const html = renderToStaticMarkup(createElement(ReportView, { investigation: investigationFixture, onOpen() {} }))
  assert.match(html, /inconclusive/)
  assert.doesNotMatch(html, /failure originates/)
  assert.match(html, /Processing may be delayed/)
  assert.match(html, /Open evidence source-b/)
  assert.match(html, /Open log workspace/)
  assert.match(html, /Analyze further/)
  assert.doesNotMatch(html, /Original usage: 2 HTTP attempts/)
  assert.match(html, /2026-11-02T00:00:09Z/)
  const rules = renderToStaticMarkup(createElement(ReportView, { investigation: { ...investigationFixture, engine: 'rules' }, onOpen() {} }))
  assert.match(rules, /Reported hypotheses/)
  assert.doesNotMatch(rules, /Analyze further/)
})

test('chronological ordering keeps source identity and missing trace relationships honest', async () => {
  const { orderedEvidence, traceCoverage } = await components('src/reports.tsx')
  const late = { id: 'late', kind: 'span', service: 'billing', name: 'authorize', timestamp: '2026-11-02T00:00:04Z', started_at: '2026-11-02T00:00:00Z', trace_id: 'trace', span_id: 'work', parent_id: 'missing' }
  const log = { id: 'timeout', kind: 'log', service: 'gateway', timestamp: '2026-11-02T00:00:02Z' }
  assert.deepEqual(orderedEvidence([log, late, late]).map(row => row.id), ['late', 'timeout'])
  assert.match(traceCoverage(late, [late, log]), /Parent span is not/)

})

test('all supplement states leave original report readable and display disagreement and separate usage', async () => {
  const { ReportView } = await components('src/assessments.tsx')
  for (const state of ['queued', 'running', 'quota_waiting', 'completed', 'failed', 'interrupted']) {
    const supplement = { id: 'supplement', investigation_id: 'saved', state, created_at: '2026-11-02T00:01:00Z', source_ids: ['source-a'], error: 'Retryable test failure.',
      usage: { requests: 1, total_tokens: 50, retry_at: new Date(Date.now() + 15000).toISOString() },
      result: state === 'completed' ? { relationship: 'disagrees', summary: 'The original conclusion is unsupported.', explanations: [{ claim: 'Delay remains unresolved.', status: 'unresolved', supporting_citations: [], conflicting_citations: ['source-a'] }], questions: [], limitations: ['No new evidence.'] } : null }
    const html = renderToStaticMarkup(createElement(ReportView, { investigation: { ...investigationFixture, supplements: [supplement] }, onOpen() {} }))
    assert.match(html, /The source is uncertain/)
    assert.doesNotMatch(html, /HTTP attempts/)
    assert.doesNotMatch(html, /Original usage: 2 HTTP attempts/)
    assert.doesNotMatch(html, />Analyze further</)
    if (state === 'completed') { assert.match(html, /disagrees/); assert.match(html, /Conflicting sources/) }
    if (state === 'quota_waiting') assert.match(html, /resumes automatically/)
    if (['failed', 'interrupted'].includes(state)) assert.match(html, /Retry further analysis/)
  }
})

test('observation inspection has readable measured fields and source punctuation is clean', async () => {
  const { ObservationFields } = await components('src/reports.tsx')
  const html = renderToStaticMarkup(createElement(ObservationFields, { row: { id: 'x', kind: 'span', service: 'payment', timestamp: '2026-11-02T00:00:04Z', duration_ms: 4000, timeout_ms: 2000, parent_id: 'caller' } }))
  assert.match(html, /Duration \(ms\)/)
  assert.match(html, /Explicit timeout \(ms\)/)
  assert.match(html, /Parent span ID/)
  for (const file of ['src/App.tsx', 'src/reports.tsx']) assert.doesNotMatch(readFileSync(file, 'utf8'), /\uFFFD|\u00c2\u00b7|\u00e2\u20ac/)
})

test('waiting and failed investigations retain activity, evidence and safe diagnostics', async () => {
  const bundle = await build({ entryPoints: ['src/assessments.tsx'], bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external' })
  const output = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), output, output.exports)
  const investigation = { id: 'fixture', engine: 'groq', state: 'quota_waiting', created_at: new Date().toISOString(), report: null,
    activity: [{ tool: 'search_logs', timestamp: new Date().toISOString(), count: 1, evidence_ids: ['abc123source'] }],
    usage: { retry_at: new Date(Date.now() + 30000).toISOString(), wait_reason: 'Waiting for tokens', requests: 1,
      diagnostics: [{ attempt: 1, phase: 'tools', status: 429, input_bytes: 1234, estimated_input_tokens: 681 }] } }
  const render = value => renderToStaticMarkup(createElement(output.exports.ReportView, { investigation: value, onOpen() {} }))
  const waiting = render(investigation)
  assert.match(waiting, /Waiting for analysis capacity/)
  assert.match(waiting, /Next attempt in \d+s/)
  assert.match(waiting, /resumes automatically/)
  assert.match(waiting, /Open evidence abc123source/)
  assert.match(waiting, /Evidence collected so far/)
  assert.doesNotMatch(waiting, /429|Provider diagnostics/)
  const failed = render({ ...investigation, state: 'failed', error: 'Daily quota exhausted.' })
  assert.match(failed, /Daily quota exhausted/)
  assert.match(failed, /Evidence collected so far/)
  assert.match(failed, /Open evidence abc123source/)
  assert.doesNotMatch(failed, /Resuming in/)
  assert.match(failed, /Retry creates a new investigation/)
})

test('TypeScript UI has no compile diagnostics', () => {
  const config = ts.readConfigFile('tsconfig.json', ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, '.')
  const program = ts.createProgram(parsed.fileNames, parsed.options)
  const diagnostics = ts.getPreEmitDiagnostics(program)
  assert.equal(diagnostics.length, 0, ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCanonicalFileName: file => file, getCurrentDirectory: () => process.cwd(), getNewLine: () => '\n',
  }))
})
test('API client rejects non-success responses and sends CSRF on writes', async () => {
  const source = readFileSync('src/api.ts', 'utf8')
  const compiled = ts.transpile(source, { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 })
  const module = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'))
  const originalFetch = globalThis.fetch
  let captured
  try {
    globalThis.fetch = async (path, options) => {
      captured = { path, options }
      return { ok: false, status: 403, json: async () => ({ error: 'Access denied' }) }
    }
    module.setCsrf('test-csrf')
    await assert.rejects(module.api('/scenarios/pool/start', {}), error => error.status === 403 && error.message === 'Access denied')
    assert.equal(captured.path, '/api/scenarios/pool/start')
    assert.equal(captured.options.headers['X-CSRF-Token'], 'test-csrf')
    assert.equal(captured.options.credentials, 'same-origin')
    globalThis.fetch = async () => new Response('Internal Server Error', { status: 500 })
    await assert.rejects(module.api('/auth/verification-confirmation', { token: 'test' }),
      error => error.status === 500 && /try again shortly/i.test(error.message) && !/Unexpected token/.test(error.message))
  } finally { globalThis.fetch = originalFetch }
})

test('slow refreshes never overlap and cancellation prevents another request', async context => {
  const { poll } = await components('src/poll.ts')
  context.mock.timers.enable({ apis: ['setTimeout'] })
  let requests = 0, finish
  const stop = poll(() => { requests++; return new Promise(resolve => { finish = resolve }) }, 2000)
  context.mock.timers.tick(20000)
  assert.equal(requests, 1)
  finish()
  await Promise.resolve()
  context.mock.timers.tick(2000)
  assert.equal(requests, 2)
  stop()
  finish()
  await Promise.resolve()
  context.mock.timers.tick(20000)
  assert.equal(requests, 2)
})

test('import form and coverage make explicit analysis and missing context clear', async () => {
  const { ImportForm, ImportCoverage } = await components('src/imports.tsx')
  const form = renderToStaticMarkup(createElement(ImportForm, { onImported() {}, onError() {} }))
  assert.match(form, /Save logs/)
  assert.match(form, /Importing does not start analysis/)
  assert.match(form, /\.log,\.txt,\.jsonl/)
  assert.match(form, /Default service/)
  assert.match(form, /1 MiB/)
  const collection = { id: 'collection', title: 'Application logs', created_at: '2026-11-03T00:00:00Z', source_type: 'paste', filename: null, symptom: '500 errors',
    summary: { observations: 3, input_lines: 5, duplicates: 1, conflicting_ids: 1, malformed_records: 1, redactions: 2, missing_timestamps: 1, missing_services: 1,
      invalid_fields: 0, kinds: ['log'], services: ['billing'], coverage: 'Only supplied text.', redaction_note: 'Review saved content.' } }
  const coverage = renderToStaticMarkup(createElement(ImportCoverage, { collection }))
  assert.match(coverage, /not a telemetry timestamp/)
  assert.match(coverage, /Conflicting IDs retained/)
  assert.match(coverage, /Malformed records kept as text/)
  assert.match(coverage, /Missing\/invalid timestamps/)
  assert.match(coverage, /No spans were supplied. No metrics were supplied/)
})

test('undated observations remain in source order without invented timestamps', async () => {
  const { orderedEvidence, FullTime } = await components('src/reports.tsx')
  const rows = [{ id: 'untimed-last', kind: 'log', timestamp: null, source_line: 6 },
    { id: 'dated', kind: 'log', timestamp: '2026-11-03T00:00:00Z', source_line: 5 },
    { id: 'untimed-first', kind: 'log', timestamp: null, source_line: 2 }]
  assert.deepEqual(orderedEvidence(rows).map(row => row.id), ['dated', 'untimed-first', 'untimed-last'])
  assert.match(renderToStaticMarkup(createElement(FullTime, { value: null })), /Timestamp not recorded/)
})

test('Sentry setup explains onboarding and pending server configuration', async () => {
  const { SentrySetup } = await components('src/connections.tsx')
  const html = renderToStaticMarkup(createElement(SentrySetup, { configured: false, publicUrl: '' }))
  assert.match(html, /Add Sentry to your application/)
  assert.match(html, /setup is pending on this server/)
  assert.match(html, /Connecting or importing never starts model analysis/)
  assert.match(html, /DSN sends telemetry/)
})

test('Sentry import cards distinguish active, incomplete and completed snapshots', async () => {
  const { SentryImportCard } = await components('src/connections.tsx')
  const item = { id: 'one', state: 'partial', created_at: '2026-11-04T00:00:00Z', parameters: { title: 'Errors' }, progress: { observations: 3 }, error: 'Rate limit exceeded.' }
  const render = state => renderToStaticMarkup(createElement(SentryImportCard, { item: { ...item, state }, busy: false, onAction() {}, onOpen() {} }))
  assert.match(render('partial'), /Retry as new snapshot/)
  assert.match(render('partial'), /3 observations retained/)
  assert.doesNotMatch(render('partial'), /Cancel import/)
  assert.match(render('running'), /Cancel import/)
  assert.doesNotMatch(render('completed'), /Retry as new snapshot|Cancel import/)
  assert.match(render('completed'), /Review evidence/)
})

test('Sentry collection coverage retains provenance and dataset access failures', async () => {
  const { ImportCoverage } = await components('src/imports.tsx')
  const html = renderToStaticMarkup(createElement(ImportCoverage, { collection: { title: 'Sentry snapshot', source_type: 'sentry', created_at: '2026-11-04T00:00:00Z', summary: {
    observations: 2, kinds: ['log'], source: { organization: 'sample-org', region: 'eu', start: '2026-11-04T00:00:00Z', end: '2026-11-04T01:00:00Z' },
    datasets: { 'payment:spans': { state: 'unavailable', reason: 'Permission missing.' } }, coverage: 'Partial Sentry snapshot.', redaction_note: 'Review saved evidence.' } } }))
  assert.match(html, /Sentry \/ sample-org/)
  assert.match(html, /payment:spans/)
  assert.match(html, /Permission missing/)
  assert.doesNotMatch(html, /pasted text/)
  assert.match(html, /Older Sentry import/)
  assert.match(html, /No spans were supplied/)
  assert.match(html, /Malformed records skipped/)
})

test('Sentry coverage separates service identity, available spans and legacy snapshots', async () => {
  const { ImportCoverage } = await components('src/imports.tsx')
  const { ConnectionRegion } = await components('src/connections.tsx')
  const { ObservationFields } = await components('src/reports.tsx')
  const html = renderToStaticMarkup(createElement(ImportCoverage, { collection: {
    title: 'Corrected snapshot', source_type: 'sentry', summary: { normalization_version: 2, observations: 6,
      kinds: ['log', 'span'], coverage: 'Bounded snapshot.', redaction_note: 'Redacted.' } } }))
  assert.match(html, /Recorded spans are available/)
  assert.match(html, /No metrics were supplied/)
  assert.match(html, /Service names identify the application/)
  assert.doesNotMatch(html, /Older Sentry import|Metrics and traces are unavailable/)
  assert.match(renderToStaticMarkup(createElement(ConnectionRegion, { region: 'eu' })), /European Union \(EU\)/)
  const fields = renderToStaticMarkup(createElement(ObservationFields, { row: {
    id: 'source', kind: 'log', service: 'record-processor', source_project: 'escalation-test',
    exception_type: 'ValueError', exception_handled: false, http_status: 422, span_op: 'http.server' } }))
  assert.match(fields, /Sentry project \(provenance\)/)
  assert.match(fields, /Exception handled<\/dt><dd>false/)
  assert.match(fields, /HTTP response status<\/dt><dd>422/)
})


test('tree placement centers parents and separates repeated calls, disconnected components and cycles', async () => {
  const { treePositions } = await components('src/lib/flow-layout.ts')
  const ids = ['root', 'left', 'right', 'leaf', 'orphan', 'cycle-a', 'cycle-b']
  const edges = [{ source: 'root', target: 'left' }, { source: 'root', target: 'right' }, { source: 'left', target: 'leaf' }, { source: 'cycle-a', target: 'cycle-b' }, { source: 'cycle-b', target: 'cycle-a' }]
  const original = JSON.stringify(edges)
  const placed = treePositions(ids, edges)
  assert.equal(placed.size, ids.length)
  assert.equal(placed.get('root').x, (placed.get('left').x + placed.get('right').x) / 2)
  assert.equal(placed.get('leaf').x, placed.get('left').x)
  assert(placed.get('orphan').x > placed.get('right').x)
  assert(placed.get('cycle-a').x > placed.get('orphan').x)
  for (const [id, point] of placed) for (const [other, next] of placed) if (id !== other) assert(Math.abs(point.x - next.x) >= 248 || Math.abs(point.y - next.y) >= 178, `${id} overlaps ${other}`)
  assert.deepEqual([...treePositions(ids, edges)], [...placed])
  assert.equal(JSON.stringify(edges), original)
  assert.equal(treePositions(Array.from({ length: 2000 }, (_, i) => String(i)), Array.from({ length: 1999 }, (_, i) => ({ source: String(i), target: String(i + 1) }))).size, 2000)
})

test('distribution totals, zero kinds, proportional services and omitted shares are exact', async () => {
  const { observationDistribution, ObservationDistribution, ServiceBarChart } = await components('src/distribution.tsx')
  const counts = { observations: 100, spans: 20, metrics: 5 }
  assert.deepEqual(observationDistribution(counts).map(row => row.value), [75, 20, 5])
  const chart = renderToStaticMarkup(createElement(ObservationDistribution, { counts }))
  assert.match(chart, /75%/); assert.match(chart, /20%/); assert.match(chart, /5%/)
  assert.match(renderToStaticMarkup(createElement(ObservationDistribution, { counts: { observations: 0, spans: 0, metrics: 0 } })), /No observations/)
  const bars = renderToStaticMarkup(createElement(ServiceBarChart, { services: [{ service: 'api', observations: 30 }, { service: 'worker', observations: 15 }], total: 60, href: service => '#/' + service }))
  assert.match(bars, /width:100%/); assert.match(bars, /width:50%/); assert.match(bars, /50% of saved/); assert.match(bars, /Other services/); assert.match(bars, /25%/)
})

test('timeline preserves undated records, precise timestamps, long content and source actions', async () => {
  const { EvidenceTimeline } = await components('src/timeline.tsx')
  const evidence = [{ id: 'first', kind: 'span', service: 'api', status: 'error', timestamp: '2026-11-07T10:00:00.123456Z', name: 'checkout', duration_ms: 4.25 }, { id: 'long', kind: 'log', service: 'api', level: 'error', timestamp: '2026-11-07T10:00:00.124Z', message: 'line content '.repeat(40) }, { id: 'unknown', kind: 'log', service: 'api', message: 'undated' }]
  const html = renderToStaticMarkup(createElement(EvidenceTimeline, { data: { evidence, offset: 100, total: 203, unknown_timestamps: 1, next_offset: 200 }, selected: 'long', onSelect: () => {}, onPage: () => {} }))
  assert.match(html, /first timed event on each page/); assert.match(html, /4.25 ms recorded/); assert.match(html, /No source timestamp/)
  assert.match(html, /Expand message/); assert.match(html, /Inspect timeline observation long/); assert.match(html, /timeline-selected/); assert.match(html, /101.*103 of 203/)
})

test('precise source times and short-range ticks retain distinct labels', async () => {
  const { sourceTime, chartTime } = await components('src/lib/time.ts')
  assert.match(sourceTime('2026-11-07T10:00:00.123456Z'), /\.123456$/)
  assert.notEqual(chartTime('2026-11-07T10:00:00.001Z', 1, 100), chartTime('2026-11-07T10:00:00.002Z', 1, 100))
  assert.notEqual(chartTime('2026-11-07T10:00:01Z', 1000, 5000), chartTime('2026-11-07T10:00:02Z', 1000, 5000))
  for (const name of ['logs', 'log-filters', 'timeline', 'dashboard']) {
    const source = readFileSync('src/' + name + '.tsx', 'utf8')
    assert(!/[\u00c2]|\u00e2[\u20ac\u2020]/.test(source), name + ' contains garbled punctuation')
  }
})
