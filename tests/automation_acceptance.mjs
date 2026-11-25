import { preview } from 'vite'
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'

const dir = process.env.DESK_AUTOMATION_ARTIFACTS || '.cache/phase12-audit/ui'
await mkdir(dir, { recursive: true })
const server = await preview({ logLevel: 'error', preview: { host: '127.0.0.1', port: 4182, strictPort: true } })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } })
const user = { id: 'owner', name: 'Acceptance fixture', email: 'fixture@example.test', role: 'regular', read_only: false, email_verified: true }
let signedIn = true, connected = true, settings = { enabled: false, projects: [], initial_email: true, result_email: true }, incidentState = 'deferred'
const requests = [], errors = [], results = [], violations = []
let holdWorkspace = null, workspaceHeld = null, rejectDisconnect = false
const notifications = [{ id: 'alert', kind: 'incident_alert', state: 'sent', delivery_status: 'email.delivered', last_error: null }]
const episode = () => ({ id: 'fixture', title: 'ValueError while parsing a supplied record', issue_id: '123', project_name: 'Dedicated Sentry test project', episode: 1, trigger: 'created', triggered_at: '2026-11-08T12:00:00+00:00', lifecycle: 'open', state: incidentState, error: incidentState === 'deferred' ? 'Daily allowance used. Automatic resumption remains available for 24 hours.' : 'Interrupted after the investigation started. Retry explicitly.', resume_until: 1794225600, report_id: null, notifications, attempts: [{ id: 'attempt', number: 1, state: incidentState, explicit_retry: false, created_at: '2026-11-08T12:00:00Z', investigation_id: null, import_id: null, error: null }] })
context.on('page', page => page.on('pageerror', error => errors.push(error.message)))
await context.route('**/api/**', async route => {
  const req = route.request(), path = new URL(req.url()).pathname
  requests.push({ path, method: req.method() })
  let body = {}, status = 200
  if (path === '/api/auth/me') { status = signedIn ? 200 : 401; body = signedIn ? { user, csrf: 'fixture' } : { error: 'Not signed in.' } }
  else if (path === '/api/auth/login') { signedIn = true; body = { user, csrf: 'fixture' } }
  else if (path === '/api/auth/logout') { signedIn = false; body = { ok: true } }
  else if (path === '/api/automatic-incidents') body = { incidents: [episode()], next_offset: null }
  else if (path === '/api/automatic-incidents/fixture') body = episode()
  else if (path.endsWith('/retry')) { incidentState = 'queued'; body = episode() }
  else if (path.endsWith('/automation')) {
    if (req.method() === 'PUT') {
      const submitted = req.postDataJSON()
      if (JSON.stringify(Object.keys(submitted).sort()) !== JSON.stringify(['enabled', 'initial_email', 'projects', 'result_email'])) {
        status = 400; body = { error: 'Provide enabled, projects, initial_email and result_email preferences.' }
      } else settings = submitted
    }
    if (status === 200) body = { ...settings, connection_id: 'connection', updated_at: '2026-11-08T12:00:00+00:00' }
  }
  else if (path.endsWith('/disconnect')) {
    if (rejectDisconnect) { status = 503; body = { error: 'Disconnect temporarily unavailable. Try again.' } }
    else { connected = false; settings.enabled = false; body = { ok: true } }
  }
  else if (path === '/api/connections/sentry') body = { configured: true, public_url: 'https://tracedesk.test', imports: [], connections: connected ? [{ id: 'connection', organization: 'fixture-org', region: 'us', state: 'connected', created_at: '2026-11-08T12:00:00Z', checked_at: null, error: null, capabilities: {}, projects: [{ id: '42', name: 'Dedicated Sentry test project', slug: 'escalation-test' }] }] : [] }
  else if (path === '/api/collections' || path === '/api/investigations') body = []
  else { status = 404; body = { error: 'Fixture unavailable.' } }
  if (path === '/api/connections/sentry' && holdWorkspace) { const wait = holdWorkspace; holdWorkspace = null; workspaceHeld(); await wait }
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
})
const page = await context.newPage()
async function capture(name, audit = true) {
  await page.evaluate(() => document.fonts.ready)
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), name + ': overflow')
  assert(!/[\u00e2][\u20ac]|\ufffd/.test(await page.locator('body').innerText()), name + ': mojibake')
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
  if (audit) violations.push(...(await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze()).violations.map(v => ({ name, id: v.id, targets: v.nodes.map(n => n.target) })))
  results.push(name)
}
try {
  for (const [width, height] of [[1280,600],[1366,650],[1440,800],[1920,900],[768,600],[761,600],[760,480],[390,650],[320,568]]) {
    await page.setViewportSize({ width, height })
    await page.goto('http://127.0.0.1:4182/incidents')
    await page.getByRole('heading', {name:'Incidents',exact:true}).waitFor()
    if (width <= 760) await page.getByRole('button',{name:'Open navigation'}).click()
    await page.evaluate(()=>document.fonts.ready)
    const geometry = await page.locator('.user-row').evaluate(el=> {
      const row=el.getBoundingClientRect(), button=el.querySelector('button').getBoundingClientRect()
      const hit=document.elementFromPoint(button.x+button.width/2,button.y+button.height/2)
      return {top:row.top,bottom:row.bottom,height:innerHeight,clickable:el.querySelector('button').contains(hit),scrollable:document.querySelector('.sidebar-scroll').scrollHeight>document.querySelector('.sidebar-scroll').clientHeight}
    })
    assert(geometry.top>=0 && geometry.bottom<=height, JSON.stringify({width,height,geometry}))
    assert(geometry.clickable, 'Logout must be unobstructed')
    await page.getByRole('button',{name:'Sign out',exact:true}).focus()
    assert(await page.getByRole('button',{name:'Sign out',exact:true}).evaluate(el=>el===document.activeElement))
    await page.screenshot({path:`${dir}/sidebar-${width}x${height}.png`})
    if (width <= 760) {
      await page.keyboard.press('Escape')
      await page.getByRole('button',{name:'Open navigation'}).waitFor()
    }
    await capture(`short-window-${width}x${height}`)
  }
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 650 })
    await page.goto('http://127.0.0.1:4182/incidents')
    await page.getByRole('heading', { name: 'Incidents', exact: true }).waitFor()
    const skip = page.getByRole('link', { name: 'Skip to content', exact: true })
    assert.equal(await skip.evaluate(el => getComputedStyle(el).clipPath), 'inset(50%)')
    await skip.focus()
    const box = await skip.boundingBox()
    const menu = await page.getByRole('button', { name: 'Open navigation' }).boundingBox()
    assert(box.y >= 8 && box.y + box.height <= 64 && box.x > menu.x + menu.width)
    await capture('skip-link-mobile-' + width)
    await page.keyboard.press('Enter')
    assert(await page.locator('main').evaluate(el => el === document.activeElement))
    assert.equal(await skip.evaluate(el => getComputedStyle(el).clipPath), 'inset(50%)')
  }
  const originalName = user.name, originalEmail = user.email
  user.name = 'Nishant Raj Extended Account Name For Responsive Verification'
  user.email = 'long.account.identifier.for.layout.verification@example.test'
  await page.setViewportSize({width:1280,height:600})
  await page.reload()
  await page.getByRole('heading',{name:'Incidents',exact:true}).waitFor()
  await capture('long-account-1280x600')
  assert(await page.locator('.user-row').evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight))
  await page.screenshot({path:`${dir}/long-account-viewport.png`})
  user.name = originalName; user.email = originalEmail
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('http://127.0.0.1:4182/incidents')
    await page.getByRole('heading', { name: 'Incidents', exact: true }).waitFor()
    await capture('incidents-' + width)
    await page.goto('http://127.0.0.1:4182/incidents/fixture')
    await page.getByRole('heading', { name: 'Incident progress' }).waitFor()
    await capture('incident-' + width)
  }
  await page.goto('http://127.0.0.1:4182/connections')
  await page.getByLabel('Enable automatic investigations').waitFor()
  assert.equal(await page.getByLabel('Enable automatic investigations').isChecked(), false)
  await page.getByLabel('Dedicated Sentry test project').first().check()
  await page.getByLabel('Enable automatic investigations').check()
  const initialSave = page.waitForResponse(response => response.url().endsWith('/automation') && response.request().method() === 'PUT')
  await page.getByRole('button', { name: 'Save automation settings' }).click()
  assert.equal((await initialSave).status(), 200)
  await page.getByText('Automation settings saved.').waitFor()
  assert(settings.enabled && settings.projects.includes('42'))
  await page.getByLabel('Email me when an incident is detected').uncheck()
  const preferenceSave = page.waitForResponse(response => response.url().endsWith('/automation') && response.request().method() === 'PUT')
  await page.getByRole('button', { name: 'Save automation settings' }).click()
  assert.equal((await preferenceSave).status(), 200)
  await page.getByText('Automation settings saved.').waitFor()
  assert.equal(settings.initial_email, false)
  assert.equal(settings.result_email, true)
  await page.reload()
  await page.getByLabel('Enable automatic investigations').waitFor()
  assert.equal(await page.getByLabel('Enable automatic investigations').isChecked(), true)
  assert.equal(await page.getByLabel('Email me when an incident is detected').isChecked(), false)
  assert.equal(await page.getByLabel('Email me the saved result or failure notice').isChecked(), true)
  results.push('settings-metadata-roundtrip-and-independent-preferences')
  await page.getByRole('heading', { name: 'Connections', exact: true }).click()
  await capture('settings-mobile')
  assert.equal(requests.filter(r => r.method === 'POST' && !r.path.startsWith('/api/auth')).length, 0)
  rejectDisconnect = true
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: 'Disconnect temporarily unavailable.' }).waitFor()
  assert.equal(connected, true)
  assert.equal(await page.getByRole('button', { name: 'Disconnect', exact: true }).isEnabled(), true)
  results.push('disconnect-failure-retains-connection')
  rejectDisconnect = false
  let releaseWorkspace
  holdWorkspace = new Promise(resolve => { releaseWorkspace = resolve })
  await new Promise(resolve => { workspaceHeld = resolve })
  const disconnected = page.waitForResponse(response => response.url().endsWith('/disconnect'))
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click()
  await disconnected
  releaseWorkspace()
  await page.getByRole('status').filter({ hasText: 'Sentry disconnected.' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Disconnect', exact: true }).count(), 0)
  assert.equal(await page.getByLabel('Enable automatic investigations').count(), 0)
  assert.equal(await page.getByRole('button', { name: 'Connect Sentry', exact: true }).isEnabled(), true)
  await capture('disconnected-mobile')
  await page.reload()
  await page.getByRole('heading', { name: 'Connections', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Connect Sentry', exact: true }).waitFor({ state: 'visible' })
  assert.equal(await page.getByRole('button', { name: 'Disconnect', exact: true }).count(), 0)
  results.push('disconnect-remains-removed-after-reload')
  signedIn = false
  await page.goto('http://127.0.0.1:4182/incidents/fixture')
  await page.getByLabel('Email address').fill(user.email)
  await page.getByLabel('Password', { exact: true }).fill('FixturePassword!26')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.getByRole('heading', { name: 'Incident progress' }).waitFor()
  assert.equal(new URL(page.url()).pathname, '/incidents/fixture')
  results.push('email-destination-after-login')
  incidentState = 'interrupted'
  await page.reload()
  await page.getByRole('button', { name: 'Retry investigation', exact: true }).click()
  await page.getByRole('heading', { name: 'Incident progress' }).waitFor()
  assert.equal(requests.filter(r => r.path.endsWith('/retry')).length, 1)
  results.push('explicit-retained-retry')
  const names = JSON.parse(await readFile('.cache/phase12/emails/index.json', 'utf8'))
  await page.route('**/*', async route => { if (route.request().resourceType() === 'image') await route.abort(); else await route.fallback() })
  for (const width of [600, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    for (const name of names) {
      await page.setContent(await readFile(`.cache/phase12/emails/${name}.html`, 'utf8'))
      await capture('email-' + name + '-' + width, false)
      assert(await page.getByRole('link').count() >= 2)
    }
  }
  assert.deepEqual(errors, [])
  assert.deepEqual(violations, [])
  console.log(JSON.stringify({ passed: results.length, provider_calls: 0, violations, errors }))
} finally {
  await writeFile(`${dir}/results.json`, JSON.stringify({ results, requests, errors, violations }, null, 2))
  await browser.close(); await new Promise(resolve => server.httpServer.close(resolve))
}
