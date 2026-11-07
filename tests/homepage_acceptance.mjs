import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import { pathToFileURL } from 'node:url'

const stepNames = ['Select evidence', 'Follow the request', 'Read assessment', 'Inspect source']
const stepIds = ['evidence', 'request', 'assessment', 'source']

export async function checkHomepageLayout(page, origin, capture) {
  const previewRequests = []
  const record = request => { if (new URL(request.url()).pathname.startsWith('/api/')) previewRequests.push(new URL(request.url()).pathname) }
  page.on('request', record)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(origin)
  await page.getByRole('heading', { name: 'Every failure leaves a trail.' }).waitFor()
  assert.equal(await page.locator('.home-feature-grid article').count(), 6)
  assert.equal(await page.locator('.home-questions details[open]').count(), 1)
  assert.equal(await page.locator('.home-page pre,.home-page .code-preview').count(), 0)
  assert(await page.getByText('Illustrative walkthrough · Sample data', { exact: true }).isVisible())
  for (const width of [1440, 1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: width > 1000 ? 1000 : 900 })
    await page.evaluate(() => window.scrollTo(0, 0))
    const heights = []
    for (let step = 0; step < stepNames.length; step++) {
      await page.getByRole('navigation', { name: 'Preview steps' }).getByRole('button', { name: stepNames[step], exact: true }).click()
      assert.equal(await page.locator('.product-preview').getAttribute('data-step'), stepIds[step])
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth ? [...document.querySelectorAll('body *')].filter(el => { const box = el.getBoundingClientRect(); return box.width && (box.right > innerWidth + 1 || box.left < -1) }).map(el => el.className).filter(Boolean).slice(0, 20) : [])
      assert.deepEqual(overflow, [], `Horizontal overflow at ${width}/${stepIds[step]}: ${overflow}`)
      heights.push((await page.locator('.product-preview').boundingBox()).height)
      await capture(`home-${width}-${stepIds[step]}`, page, true)
    }
    assert(Math.max(...heights) - Math.min(...heights) <= 2, `Preview jumps at ${width}: ${heights}`)
    if (width <= 500) {
      assert(Math.max(...heights) <= 740, `Mobile preview is too tall at ${width}: ${heights}`)
      const context = page.locator('.preview-mobile-context')
      await context.locator('summary').click()
      assert.equal(await context.locator('.sample-log-row:visible').count(), 6)
      assert(await context.getByText('Retained source', { exact: true }).isVisible())
      await context.locator('summary').click()
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  for (const [source, text] of [['05', 'Null reference at Billing.java:42'], ['06', 'Checkout returned HTTP 500']]) {
    await page.getByRole('button', { name: 'Read assessment', exact: true }).click()
    await page.getByRole('button', { name: new RegExp(text.replaceAll('.', '\\.')) }).click()
    assert.equal(await page.locator('.preview-scene-panel.is-active .sample-log-row.is-selected .sample-message > span').innerText(), source === '05' ? 'Invoice reference is null' : 'Checkout returned HTTP 500')
    assert((await page.locator('.preview-inspector-panel.is-active .sample-inspector').innerText()).includes(source))
    assert(await page.locator('.preview-scene').evaluate(element => element === document.activeElement))
  }
  const hash = new URL(page.url()).hash
  for (const section of ['Features', 'Workflow', 'FAQ']) {
    await page.getByRole('navigation', { name: 'Homepage sections' }).getByRole('button', { name: section, exact: true }).click()
    assert.equal(new URL(page.url()).hash, hash)
    assert.equal(await page.evaluate(() => document.activeElement.id), section.toLowerCase())
  }
  await page.getByText('Do I need a Sentry connection?', { exact: true }).click()
  assert.equal(await page.locator('.home-questions details[open]').count(), 2)
  await page.getByText('What can I bring into TraceDesk?', { exact: true }).click()
  assert.equal(await page.locator('.home-questions details[open]').count(), 1)
  page.off('request', record)
  assert(previewRequests.every(path => path === '/api/auth/me'), `Preview made extra requests: ${previewRequests}`)
  for (const [selector, hash, heading] of [
    ['.home-nav a[href="#/signup"]', '#/signup', 'Create your account.'],
    ['.home-nav a[href="#/login"]', '#/login', 'Sign in to TraceDesk.'],
    ['.hero a[href="#/demo-login"]', '#/demo-login', 'Explore a real recording.'],
  ]) {
    await page.goto(origin)
    await page.locator(selector).click()
    assert.equal(new URL(page.url()).hash, hash)
    await page.getByRole('heading', { name: heading, exact: true }).waitFor()
    await capture(`home-destination-${hash.slice(2)}`, page, true)
  }
  await page.goto(origin)
  await page.locator('.home-footer a[href="#/privacy"]').click()
  await page.getByRole('heading', { name: 'Data use & processing' }).waitFor()
  await page.getByText('Provider and processing details', { exact: true }).click()
  assert(await page.getByText(/TypeSafe Jev through OpenRouter/).isVisible())
  await capture('home-destination-privacy', page, true)
  await page.goto(origin)
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  assert.equal(await page.evaluate(() => document.activeElement.id), 'main-content')
  const firstStep = page.getByRole('button', { name: 'Select evidence', exact: true })
  await firstStep.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  assert.equal(await page.locator('.product-preview').getAttribute('data-step'), 'request')
  assert.equal(await page.locator('.preview-navigation button.is-active').evaluate(el => getComputedStyle(el).outlineStyle), 'solid')
  // A 640 CSS-pixel viewport models desktop reflow at 200% browser zoom.
  await page.setViewportSize({ width: 640, height: 450 })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await capture('home-zoom-reflow', page, true)
  await page.setViewportSize({ width: 1440, height: 1000 })
}

export async function checkHomepagePlayback(page, origin) {
  const playback = await page.context().newPage()
  try {
    await playback.emulateMedia({ reducedMotion: 'no-preference' })
    await playback.clock.install()
    await playback.goto(origin)
    const preview = playback.locator('.product-preview')
    await preview.waitFor()
    await playback.mouse.move(0, 0)
    await playback.waitForFunction(() => document.querySelector('.product-preview')?.dataset.paused === 'false')
    for (const expected of ['request', 'assessment', 'source', 'evidence']) {
      await playback.clock.runFor(6100)
      assert.equal(await preview.getAttribute('data-step'), expected)
    }
    await preview.hover()
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'evidence')
    await playback.mouse.move(0, 0)
    await playback.getByRole('button', { name: 'Follow the request', exact: true }).focus()
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'evidence')
    await playback.keyboard.press('Enter')
    await playback.locator('.hero h1').click()
    await playback.mouse.move(0, 0)
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'request', 'Manual navigation must stay paused')
    await playback.getByRole('button', { name: 'Play walkthrough' }).click()
    await playback.locator('.hero h1').click()
    await playback.mouse.move(0, 0)
    await playback.clock.runFor(6100)
    assert.equal(await preview.getAttribute('data-step'), 'assessment')
    await playback.getByRole('button', { name: 'Pause walkthrough' }).click()
    await playback.locator('.hero h1').click()
    await playback.mouse.move(0, 0)
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'assessment')
    await playback.getByRole('button', { name: 'Play walkthrough' }).click()
    await playback.locator('.home-wordmark').scrollIntoViewIfNeeded()
    await playback.mouse.move(0, 0)
    await playback.waitForFunction(() => document.querySelector('.product-preview')?.dataset.paused === 'true')
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'assessment')
    await playback.locator('.hero h1').click()
    await playback.mouse.move(0, 0)
    await playback.waitForFunction(() => document.querySelector('.product-preview')?.dataset.paused === 'false')
    await playback.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')) })
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'assessment')
    await playback.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')) })
    await playback.clock.runFor(6100)
    assert.equal(await preview.getAttribute('data-step'), 'source')
    await playback.emulateMedia({ reducedMotion: 'reduce' })
    await playback.waitForFunction(() => document.querySelector('.product-preview')?.dataset.autoplay === 'false')
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'source')
    await playback.reload()
    await preview.waitFor()
    await playback.clock.runFor(6500)
    assert.equal(await preview.getAttribute('data-step'), 'evidence')
    assert(await playback.getByRole('button', { name: 'Play walkthrough' }).isDisabled())
    await playback.getByRole('button', { name: 'Read assessment', exact: true }).click()
    assert.equal(await preview.getAttribute('data-step'), 'assessment')
  } finally { await playback.close() }
}

async function standalone() {
  const dir = '.cache/homepage/browser'
  const port = Number(process.env.DESK_HOME_PORT || 4175)
  const origin = `http://127.0.0.1:${port}`
  await mkdir(dir, { recursive: true })
  const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { windowsHide: true, stdio: 'pipe' })
  let serverOutput = ''
  server.stdout.on('data', chunk => { serverOutput += chunk })
  server.stderr.on('data', chunk => { serverOutput += chunk })
  let browser
  const results = [], violations = [], errors = [], apiRequests = []
  try {
    for (let attempt = 0; ; attempt++) {
      if (server.exitCode !== null) throw new Error(serverOutput)
      try { if ((await fetch(origin)).ok) break } catch {}
      if (attempt >= 60) throw new Error('Preview startup timed out: ' + serverOutput)
      await new Promise(resolve => setTimeout(resolve, 250))
    }
    browser = await chromium.launch({ headless: true })
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
    context.on('page', surface => surface.on('pageerror', error => errors.push(error.message)))
    await context.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      apiRequests.push({ path, method: route.request().method() })
      return route.fulfill({ status: path === '/api/auth/me' ? 401 : path === '/api/health' ? 200 : 503, contentType: 'application/json', body: JSON.stringify(path === '/api/health' ? { demo_available: true, demo_case_count: 6 } : { error: 'Test session: not signed in' }) })
    })
    const page = await context.newPage()
    const capture = async (name, surface, audit) => {
      await surface.evaluate(() => document.fonts.ready)
      await surface.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
      if (/^home-(320|390)-/.test(name)) await surface.locator('.product-preview').screenshot({ path: `${dir}/${name}-preview.png` })
      if (audit) {
        const report = await new AxeBuilder({ page: surface }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()
        violations.push(...report.violations.map(item => ({ screen: name, id: item.id, nodes: item.nodes.map(node => node.target) })))
      }
    }
    await checkHomepageLayout(page, origin, capture)
    results.push('Responsive layouts, all four scenes, both citations, navigation, FAQ, keyboard and privacy')
    console.log('PASS homepage layouts and navigation')
    await checkHomepagePlayback(page, origin)
    results.push('Autoplay loop, hover/focus/manual/pause controls, viewport, visibility and reduced motion')
    assert(apiRequests.every(({ path, method }) => method === 'GET' && ['/api/auth/me', '/api/health'].includes(path)), JSON.stringify(apiRequests))
    results.push('No evidence, investigation, provider or mutation requests')
    assert.deepEqual(errors, [])
    assert.deepEqual(violations, [])
    console.log('PASS homepage playback, accessibility and request isolation')
  } catch (error) {
    errors.push(error.stack)
    process.exitCode = 1
    console.error(error)
  } finally {
    await writeFile(`${dir}/acceptance.json`, JSON.stringify({ results, violations, errors, apiRequests, backend: 'mocked session and health responses; no database or providers' }, null, 2))
    await browser?.close()
    server.kill()
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await standalone()
