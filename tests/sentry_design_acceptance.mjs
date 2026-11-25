// Reference-design checks against the production build; no external OAuth or providers.
import { preview } from 'vite'
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'

const dir = '.cache/sentry-design/browser'
await mkdir(dir, { recursive: true })
const server = await preview({ logLevel: 'error', preview: { host: '127.0.0.1', port: 4191, strictPort: true } })
const origin = 'http://127.0.0.1:4191'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
const user = { id: 'design-fixture', name: 'Design fixture', email: 'fixture@tracedesk.test', role: 'regular', read_only: false, email_verified: true }
const connected = { id: 'connection', organization: 'Fixture organization', region: 'us', state: 'connected', created_at: '2026-11-08T10:00:00Z', checked_at: null, error: null, projects: [], capabilities: {} }
const imported = { id: 'import', collection_id: 'saved', state: 'completed', created_at: '2026-11-08T10:00:00Z', parameters: { title: 'Retained snapshot' }, progress: { observations: 12 }, error: null }
let signedIn = false, workspace = { configured: true, public_url: '', connections: [], imports: [] }, loadStatus = 200, hold = null
const writes = [], results = [], violations = [], errors = []
context.on('page', surface => surface.on('pageerror', e => errors.push(e.message)))
await context.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname, post = route.request().method() === 'POST'
  let status = 200, body = {}
  if (post) writes.push({ path, email: route.request().postDataJSON()?.email, region: route.request().postDataJSON()?.region })
  if (path === '/api/auth/me') { status = signedIn ? 200 : 401; body = signedIn ? { user, csrf: 'fixture' } : { error: 'Not signed in' } }
  else if (path === '/api/health') body = { demo_available: true, demo_case_count: 6 }
  else if (path === '/api/auth/login' || path === '/api/auth/signup') { status = 400; body = { error: 'Fixture login rejected. Check your email and password.' } }
  else if (path === '/api/connections/sentry') { if (hold) await hold; status = loadStatus; body = loadStatus === 200 ? workspace : { error: 'Connection lookup temporarily unavailable.' } }
  else if (path === '/api/connections/sentry/start') body = { url: origin + '/connections?oauth=connected' }
  else if (path.endsWith('/check')) { workspace = { ...workspace, connections: [{ ...connected, projects: [{ id: 'p', name: 'Checkout', slug: 'checkout' }] }] }; body = { ok: true } }
  else if (path.endsWith('/disconnect')) { workspace = { ...workspace, connections: [{ ...connected, state: 'disconnected' }] }; body = { ok: true } }
  else { status = 404; body = { error: 'Unexpected fixture request.' } }
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
})
const page = await context.newPage()
async function check(name, task) { await task(); results.push(name); console.log('PASS ' + name) }
async function capture(name, audit = true) {
  await page.evaluate(() => document.fonts.ready)
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), name + ': overflow')
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
  if (audit) {
    const report = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()
    violations.push(...report.violations.map(v => ({ name, id: v.id, nodes: v.nodes.map(n => n.target) })))
  }
}
try {
  await check('rounded auth fields and submit buttons retain native form behavior', async () => {
    signedIn = false
    for (const mode of ['login', 'signup']) {
      await page.goto(origin + '/' + mode)
      await page.getByLabel('Email address').waitFor()
      assert.equal(await page.locator('.auth-input-well').count(), mode === 'signup' ? 3 : 2)
      assert.equal(await page.locator('.auth-input-well .auth-field-icon').count(), mode === 'signup' ? 3 : 2)
      assert.equal(await page.locator('.auth-form-wrap form > .button').evaluate(el => getComputedStyle(el).borderRadius), '999px')
      assert.equal(await page.getByLabel('Email address').getAttribute('autocomplete'), 'email')
      assert.equal(await page.getByLabel('Password', { exact: true }).getAttribute('autocomplete'), mode === 'signup' ? 'new-password' : 'current-password')
      if (mode === 'signup') {
        assert.equal(await page.getByLabel('Your name').evaluate(el => el.closest('.auth-input-well') !== null), true)
        assert.equal(await page.getByLabel('Your name').getAttribute('autocomplete'), 'name')
      }
      for (const width of [320, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: width < 768 ? 900 : 1000 })
        assert.equal(Math.round((await page.locator('.auth-input-well').first().boundingBox()).height), 54)
        await capture(`${mode}-${width}`)
      }
      await page.setViewportSize({ width: 1280, height: 592 })
      assert(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight), 'Auth form fits the short desktop viewport')
      await capture(`${mode}-short-desktop`)
      await page.setViewportSize({ width: 1440, height: 1000 })
      if (mode === 'signup') await page.getByLabel('Your name').fill('Design fixture')
      await page.getByLabel('Email address').fill(user.email)
      await page.getByLabel('Password', { exact: true }).fill('FixturePassword!26')
      await page.getByLabel('Email address').focus()
      assert.equal(await page.getByLabel('Email address').evaluate(el => getComputedStyle(el.closest('.auth-input-well')).outlineStyle), 'solid')
      await page.getByRole('button', { name: 'Show password', exact: true }).click()
      assert.equal(await page.getByLabel('Password', { exact: true }).getAttribute('type'), 'text')
      await page.getByRole('button', { name: 'Hide password', exact: true }).click()
      const before = writes.length
      await page.getByLabel('Password', { exact: true }).press('Enter')
      await page.getByRole('alert').filter({ hasText: 'Fixture login rejected' }).waitFor()
      assert.equal(writes.length, before + 1)
      assert.equal(writes.at(-1).path, '/api/auth/' + mode)
      assert.equal(writes.at(-1).email, user.email)
      assert.equal(await page.getByLabel('Email address').inputValue(), user.email)
    }
    await page.goto(origin + '/account#auth/reset/' + 'fixture_token_'.repeat(4))
    await page.getByLabel('New password', { exact: true }).waitFor()
    assert.equal(await page.locator('.auth-input-well').count(), 0)
    await page.goto(origin + '/demo-login')
    await page.getByRole('button', { name: 'Enter recorded demo' }).waitFor()
    assert.equal(await page.locator('.auth-input-well').count(), 0)
    assert.equal(await page.getByRole('button', { name: 'Enter recorded demo' }).evaluate(el => getComputedStyle(el).borderRadius), '999px')
    for (const width of [320, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: width < 768 ? 900 : 1000 })
      await capture(`demo-login-${width}`)
    }
  })
  await check('pending and failed connections never masquerade as empty or unconfigured; retry works', async () => {
    signedIn = true
    let release
    hold = new Promise(resolve => { release = resolve })
    await page.goto(origin + '/connections')
    await page.getByRole('status').filter({ hasText: 'Loading Sentry connections' }).waitFor()
    assert.equal(await page.locator('.sentry-empty-panel').count(), 0)
    assert.equal(await page.getByText('Sentry setup is pending', { exact: true }).count(), 0)
    loadStatus = 503; hold = null; release()
    await page.getByRole('button', { name: 'Try again' }).waitFor()
    assert.equal(await page.locator('.sentry-empty-panel').count(), 0)
    await capture('connections-failed')
    loadStatus = 200
    await page.getByRole('button', { name: 'Try again' }).click()
    await page.getByRole('heading', { name: 'Connect Sentry to bring your evidence together' }).waitFor()
    workspace = null; await page.reload()
    await page.getByRole('alert').filter({ hasText: 'could not be loaded' }).waitFor()
    assert.equal(await page.locator('.sentry-empty-panel').count(), 0)
    workspace = { configured: true, public_url: '', connections: [], imports: [] }
    await page.getByRole('button', { name: 'Try again' }).click()
    await page.getByRole('heading', { name: 'Connect Sentry to bring your evidence together' }).waitFor()
  })
  await check('reference empty panel fits all five widths and OAuth keeps the selected region', async () => {
    for (const width of [320, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: width < 768 ? 900 : 1000 })
      await page.evaluate(() => scrollTo(0, 0))
      assert.equal(await page.locator('.sentry-empty-panel .connection-illustration').count(), 1)
      await capture(`connections-empty-${width}`)
    }
    await page.getByLabel('Region for new authorization').selectOption('eu')
    const before = writes.length
    await page.getByRole('button', { name: 'Connect Sentry', exact: true }).click()
    await page.waitForURL(origin + '/connections?oauth=connected')
    assert.equal(writes.length, before + 1)
    assert.equal(writes.at(-1).path, '/api/connections/sentry/start')
    assert.equal(writes.at(-1).region, 'eu')
  })
  await check('unconfigured setup disables Connect and preserves saved imports and expandable guidance', async () => {
    workspace = { configured: false, public_url: '', connections: [], imports: [imported] }
    await page.reload()
    await page.getByRole('heading', { name: 'Sentry setup is pending', exact: true }).waitFor()
    assert(await page.getByRole('button', { name: 'Connect Sentry', exact: true }).isDisabled())
    await page.getByText('Retained snapshot', { exact: true }).waitFor()
    await page.getByText('Connection setup & data use', { exact: true }).click()
    await page.getByText('Add Sentry to your application and send errors, logs and traces to your own project.', { exact: true }).waitFor()
    await capture('connections-unconfigured')
  })
  await check('zero projects and disconnected organizations keep actionable guidance and snapshots', async () => {
    workspace = { configured: true, public_url: '', connections: [connected], imports: [imported] }
    await page.reload()
    await page.getByRole('heading', { name: 'No authorized projects found', exact: true }).waitFor()
    assert(await page.getByRole('button', { name: 'Import evidence', exact: true }).isDisabled())
    await capture('connections-no-projects')
    await page.getByRole('button', { name: 'Check access', exact: true }).click()
    await page.getByText('Checkout', { exact: false }).first().waitFor()
    await page.getByRole('button', { name: 'Disconnect', exact: true }).click()
    await page.getByText('Use Connect Sentry to authorize this organization again.', { exact: false }).waitFor()
    await page.getByText('Retained snapshot', { exact: true }).waitFor()
    assert.equal(await page.getByRole('heading', { name: 'Connect Sentry to bring your evidence together' }).count(), 0)
    await capture('connections-disconnected')
  })
  assert.deepEqual(errors, [])
  assert.deepEqual(violations, [])
  assert(writes.every(r => ['/api/auth/login', '/api/auth/signup', '/api/connections/sentry/start', '/api/connections/sentry/connection/check', '/api/connections/sentry/connection/disconnect'].includes(r.path)), 'Only explicit user actions may write')
} catch (error) {
  errors.push(error.stack); console.error(error); process.exitCode = 1
  await page.screenshot({ path: `${dir}/failure.png`, fullPage: true }).catch(() => {})
} finally {
  await writeFile(`${dir}/acceptance.json`, JSON.stringify({ results, errors, violations, writes, providers: 'local mocked fixtures only' }, null, 2))
  await browser.close(); await new Promise(resolve => server.httpServer.close(resolve))
}
