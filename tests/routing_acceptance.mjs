// Clean URL and session flows against the production build, with local API fixtures.
import { preview } from 'vite'
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'

const dir = '.cache/clean-urls/browser'
await mkdir(dir, { recursive: true })
const server = await preview({ logLevel: 'error', preview: { host: '127.0.0.1', port: 4178, strictPort: true } })
const origin = 'http://127.0.0.1:4178'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } })
const regular = { id: 'fixture', name: 'Routing fixture', email: 'owner@tracedesk.test', role: 'regular', read_only: false, email_verified: true }
const demo = { ...regular, role: 'demo', read_only: true, email: 'demo@escalationdesk.invalid' }
let user = null, meStatus = 200, holdMe = null, tokenUsed = false
const requests = [], results = [], errors = [], violations = []
context.on('page', surface => surface.on('pageerror', e => errors.push(e.message)))
await context.route('**/api/**', async route => {
  const request = route.request(), path = new URL(request.url()).pathname
  requests.push({ path, method: request.method() })
  let body = {}, status = 200
  if (path === '/api/auth/me') { if (holdMe) await holdMe; status = meStatus !== 200 ? meStatus : user ? 200 : 401; body = { user, csrf: 'fixture' } }
  else if (path === '/api/auth/login' || path === '/api/auth/signup') { user = user || regular; body = { user, csrf: 'fixture' } }
  else if (path === '/api/auth/demo-login') { user = demo; body = { user, csrf: 'fixture' } }
  else if (path === '/api/auth/logout') { user = null; body = { ok: true } }
  else if (path === '/api/auth/verification-confirmation') { status = tokenUsed ? 400 : 200; tokenUsed = true; if (user) user = { ...user, email_verified: true }; body = { message: 'Email verified.' } }
  else if (path === '/api/auth/reset-password') { status = tokenUsed ? 400 : 200; tokenUsed = true; user = null; body = { message: 'Password changed. Sign in again.' } }
  else if (path === '/api/health') body = { demo_available: true, demo_case_count: 6 }
  else if (path === '/api/collections' || path === '/api/investigations') body = []
  else if (path === '/api/demo/cases') body = []
  else { status = 404; body = { error: 'Saved fixture unavailable.' } }
  if (status >= 400) body = { error: status === 401 ? 'Not signed in.' : status === 503 ? 'Session lookup temporarily unavailable.' : 'This link is invalid or expired.' }
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
})
const page = await context.newPage()
async function capture(name, audit = true) {
  await page.evaluate(() => document.fonts.ready)
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal overflow`)
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
  if (audit) {
    const report = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()
    violations.push(...report.violations.map(v => ({ name, id: v.id, nodes: v.nodes.map(n => n.target) })))
  }
}
async function check(name, task) { if (process.env.DESK_ROUTING_ONLY && !new RegExp(process.env.DESK_ROUTING_ONLY).test(name)) return; await task(); results.push(name); console.log('PASS ' + name) }
async function login() { await page.getByLabel('Email address').fill(regular.email); await page.getByLabel('Password', { exact: true }).fill('FixturePassword!26'); await page.getByRole('button', { name: 'Sign in', exact: true }).click() }
try {
  await check('slow session lookup reserves actions without guest CTA flash; failure is retryable', async () => {
    let release
    holdMe = new Promise(resolve => { release = resolve })
    await page.goto(origin)
    await page.getByRole('status').filter({ hasText: 'Checking your session' }).waitFor()
    assert.equal(await page.locator('.home-page a[href="/signup"],.home-page a[href="/demo-login"]').count(), 0)
    const size = await page.locator('.hero-actions').boundingBox()
    await capture('home-pending')
    meStatus = 503; holdMe = null; release()
    await page.getByRole('button', { name: 'Try again', exact: true }).waitFor()
    assert.equal(await page.locator('.home-page a[href="/signup"]').count(), 0)
    assert.equal(await page.locator('.home-action-placeholder,.home-nav-placeholder').count(), 0)
    await capture('home-session-error')
    for (const width of [320, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: width < 768 ? 900 : 1000 })
      assert(await page.getByRole('button', { name: 'Try again', exact: true }).isVisible())
      await capture(`home-session-error-${width}`)
    }
    meStatus = 200; user = regular
    await page.getByRole('button', { name: 'Try again', exact: true }).click()
    await page.getByRole('link', { name: 'Dashboard', exact: true }).waitFor()
    assert.equal(await page.locator('.home-page a[href="/signup"]').count(), 0)
    assert(Math.abs(size.height - (await page.locator('.hero-actions').boundingBox()).height) < 5)
  })
  await check('regular and demo homepage actions, compact menu, focus and responsive layouts', async () => {
    for (const [role, account] of [['regular', regular], ['demo', demo]]) {
      user = account; await page.goto(origin)
      await page.getByRole('link', { name: 'Dashboard', exact: true }).waitFor()
      const secondary = role === 'regular' ? '/collections' : '/demo'
      for (const region of ['.hero', '.home-workflow', '.home-faq', '.home-cta', '.home-footer']) assert(await page.locator(`${region} a[href="${secondary}"]`).count())
      assert.equal(await page.locator('.home-page a[href="/demo-login"],.home-page a[href="/signup"],.home-page a[href="/login"]').count(), 0)
      for (const width of [320, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: width < 768 ? 900 : 1000 })
        await page.evaluate(() => scrollTo(0, 0))
        if (width < 768) {
          assert.equal(await page.locator('.home-account-nav').isVisible(), false)
          const summary = page.getByLabel('Homepage menu')
          await summary.focus(); await page.keyboard.press('Enter')
          assert(await page.getByRole('navigation', { name: 'Mobile homepage sections' }).getByRole('link', { name: 'Dashboard', exact: true }).isVisible())
          await capture(`home-${role}-${width}-menu`)
          await page.keyboard.press('Escape')
          assert(await summary.evaluate(el => el === document.activeElement))
          await summary.click()
          await page.getByRole('navigation', { name: 'Mobile homepage sections' }).getByRole('button', { name: 'Workflow', exact: true }).click()
          assert.equal(await page.evaluate(() => document.activeElement.id), 'workflow')
          assert.equal(new URL(page.url()).hash, '')
          assert.equal(await page.locator('.home-mobile-menu').getAttribute('open'), null)
          if (width === 320) {
            await summary.click()
            await page.getByRole('navigation', { name: 'Mobile homepage sections' }).getByRole('link', { name: 'Dashboard', exact: true }).click()
            await page.waitForURL(origin + '/dashboard')
            await page.goBack()
            await page.locator('.home-page[data-session-status="authenticated"]').waitFor()
          }
        } else assert(await page.getByRole('link', { name: 'Dashboard', exact: true }).isVisible())
        await page.evaluate(() => scrollTo(0, 0))
        await capture(`home-${role}-${width}`)
      }
    }
  })
  await check('guest login opens Dashboard while protected report and evidence links resume exactly', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    for (const [path, expected] of [
      ['/login', '/dashboard'],
      ['/investigations/report%20id', '/investigations/report%20id'],
      ['/logs?investigation=report%20id&evidence=source&trace_id=trace-A', '/logs?investigation=report%20id&evidence=source&trace_id=trace-A'],
    ]) {
      user = null; await page.goto(origin + path)
      await page.getByLabel('Email address').waitFor()
      await page.evaluate(() => sessionStorage.setItem('tracedesk:return-to', '#/logs?evidence=old'))
      await login()
      const expectedUrl = new URL(expected, origin)
      await page.waitForURL(url => url.pathname === expectedUrl.pathname && url.searchParams.toString() === expectedUrl.searchParams.toString())
      assert.equal(await page.evaluate(() => sessionStorage.getItem('tracedesk:return-to')), null)
    }
    user = null; await page.goto(origin + '/demo-login')
    await page.getByRole('button', { name: 'Enter recorded demo' }).click()
    await page.waitForURL(origin + '/dashboard')
    await page.getByText('Recorded demo account', { exact: true }).waitFor()
    assert(!(await page.locator('body').innerText()).includes('demo@escalationdesk.invalid'))
  })
  await check('existing sessions retain direct paths, encoded IDs and filters; auth routes redirect', async () => {
    user = regular
    for (const path of ['/collections/saved%20%2F%20id', '/investigations/report%3F%23', '/logs?evidence=a%26b&query=Caused+by%3A&service=billing']) {
      await page.goto(origin + path); await page.locator('.app-shell').waitFor()
      assert.equal(new URL(page.url()).pathname + new URL(page.url()).search, path)
      await page.reload(); await page.locator('.app-shell').waitFor()
      assert.equal(new URL(page.url()).pathname + new URL(page.url()).search, path)
    }
    for (const path of ['/login', '/signup', '/demo-login']) { await page.goto(origin + path); await page.waitForURL(origin + '/dashboard') }
  })
  await check('legacy bare and slash hashes and path aliases normalize with replacement', async () => {
    user = regular
    for (const [old, expected] of [['/#/dashboard', '/dashboard'], ['/#overview', '/dashboard'], ['/#history/report%20id', '/investigations/report%20id'], ['/#evidence/saved', '/collections/saved'], ['/#imports', '/import'], ['/#connections?oauth=connected', '/connections?oauth=connected'], ['/#/logs?evidence=a%26b', '/logs?evidence=a%26b'], ['/history/report', '/investigations/report']]) {
      await page.goto(origin + old); await page.locator('.app-shell').waitFor()
      assert.equal(new URL(page.url()).pathname + new URL(page.url()).search, expected)
      assert.equal(new URL(page.url()).hash, '')
    }
  })
  await check('internal links use History API; Back, Forward, new tabs and native click exceptions work', async () => {
    user = regular; await page.goto(origin); await page.getByRole('link', { name: 'Dashboard', exact: true }).waitFor()
    await page.evaluate(() => { window.routingMarker = 'same document' })
    await page.getByRole('link', { name: 'Dashboard', exact: true }).click(); await page.waitForURL(origin + '/dashboard')
    await page.getByRole('navigation', { name: 'Workspace', exact: true }).getByRole('link', { name: 'Saved evidence', exact: true }).click()
    await page.waitForURL(origin + '/collections')
    await page.goBack(); await page.waitForURL(origin + '/dashboard')
    await page.goBack(); await page.getByRole('heading', { name: 'Every failure leaves a trail.' }).waitFor()
    await page.goForward(); await page.waitForURL(origin + '/dashboard')
    assert.equal(await page.evaluate(() => window.routingMarker), 'same document')
    await page.evaluate(() => {
      const current = location.href
      const exceptions = [{ href: '/collections', ctrlKey: true }, { href: '/collections', metaKey: true }, { href: '/collections', shiftKey: true }, { href: '/collections', altKey: true }, { href: '/collections', button: 1 }, { href: '/collections', target: '_blank' }, { href: '/collections', download: 'evidence' }, { href: 'https://example.test/' }, { href: '#main-content' }, { href: '/assets/missing.js' }]
      for (const options of exceptions) {
        const a = document.createElement('a'); a.href = options.href
        if (options.target) a.target = options.target
        if (options.download) a.download = options.download
        document.body.append(a)
        let intercepted = false
        const observe = event => { intercepted = event.defaultPrevented; event.preventDefault() }
        window.addEventListener('click', observe, { once: true })
        a.dispatchEvent(new MouseEvent('click', { ...options, bubbles: true, cancelable: true }))
        a.remove()
        if (intercepted || location.href !== current) throw new Error('Native exception intercepted: ' + options.href)
      }
      const a = document.createElement('a'); a.href = '/privacy'; a.target = '_blank'; a.id = 'native-new-tab'; a.textContent = 'Native new tab'; a.style.cssText = 'position:fixed;top:10px;right:10px;z-index:9999;background:black;padding:12px'; document.body.append(a)
    })
    const [tab] = await Promise.all([context.waitForEvent('page'), page.locator('#native-new-tab').click()])
    await tab.waitForURL(origin + '/privacy'); await tab.getByRole('heading', { name: 'Data use & processing' }).waitFor(); await tab.close()
  })
  await check('unverified accounts receive guidance and verification completes at Dashboard', async () => {
    user = { ...regular, email_verified: false }; await page.goto(origin)
    await page.getByText('Verify your email to open your workspace.', { exact: false }).waitFor()
    assert.equal(await page.locator('.home-page a[href="/collections"],.home-page a[href="/demo-login"]').count(), 0)
    await capture('home-unverified')
    await page.getByRole('link', { name: 'Dashboard', exact: true }).click()
    await page.getByRole('heading', { name: 'Verify your email to continue.' }).waitFor()
    tokenUsed = false
    await page.goto(origin + '/account#auth/verify/' + 'fixture_token_'.repeat(4))
    await page.getByRole('button', { name: 'Confirm email address' }).click()
    await page.getByRole('button', { name: 'Continue to workspace' }).click()
    await page.waitForURL(origin + '/dashboard')
  })
  await check('old and new account links hide fragments and consume tokens only on explicit submission', async () => {
    for (const prefix of ['/#auth/', '/account#auth/']) for (const purpose of ['verify', 'reset']) {
      user = null; tokenUsed = false
      const token = 'private_token_'.repeat(4), before = requests.filter(r => r.method === 'POST').length
      await page.goto(origin + prefix + purpose + '/' + token)
      await page.getByRole('heading', { name: purpose === 'verify' ? 'Verify your email.' : 'Choose a new password.' }).waitFor()
      assert.equal(page.url(), origin + '/account')
      assert(!(await page.content()).includes(token))
      assert.equal(requests.filter(r => r.method === 'POST').length, before)
      if (purpose === 'reset') { await page.getByLabel('New password', { exact: true }).fill('ChangedPassword!26'); await page.getByLabel('Confirm new password', { exact: true }).fill('ChangedPassword!26') }
      await page.getByRole('button', { name: purpose === 'verify' ? 'Confirm email address' : 'Change password', exact: true }).click()
      await page.getByRole('heading', { name: purpose === 'verify' ? 'Email verified.' : 'Password changed.' }).waitFor()
      if (purpose === 'reset') { await page.getByRole('button', { name: 'Return to sign in' }).click(); await page.waitForURL(origin + '/login') }
    }
    await page.goto(origin + '/account#auth/reset/short')
    await page.getByRole('heading', { name: 'This link is invalid.' }).waitFor()
    assert.equal(page.url(), origin + '/account')
  })
  await check('logout and session expiry immediately update homepage actions', async () => {
    user = regular; await page.goto(origin + '/dashboard'); await page.getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL(origin + '/login'); await page.getByRole('link', { name: 'Back to TraceDesk' }).click()
    await page.locator('.hero a[href="/signup"]').waitFor()
    user = regular; await page.reload(); await page.getByRole('link', { name: 'Dashboard', exact: true }).waitFor()
    user = null
    await page.evaluate(() => window.dispatchEvent(new Event('tracedesk:session-expired')))
    await page.locator('.hero a[href="/signup"]').waitFor()
    assert.equal(await page.locator('.home-page a[href="/dashboard"]').count(), 0)
  })
  assert.deepEqual(errors, [])
  assert.deepEqual(violations, [])
} catch (error) {
  errors.push(error.stack); console.error(error); process.exitCode = 1
  await page.screenshot({ path: `${dir}/failure.png`, fullPage: true }).catch(() => {})
} finally {
  await writeFile(`${dir}/acceptance.json`, JSON.stringify({ results, violations, errors, requests, backend: 'local mocked fixtures' }, null, 2))
  await browser.close(); await new Promise(resolve => server.httpServer.close(resolve))
}
