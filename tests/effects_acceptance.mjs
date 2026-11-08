// Production-build UI checks with local fixtures. No backend, OAuth or model calls.
import { preview } from 'vite'
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'

const dir = '.cache/originkit/browser'
await mkdir(dir, { recursive: true })
const server = await preview({ logLevel: 'error', preview: { host: '127.0.0.1', port: 4176, strictPort: true } })
const origin = 'http://127.0.0.1:4176'
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] })
const results = [], errors = [], violations = [], requests = []
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
const user = { id: 'visual-user', name: 'Visual fixture', email: 'visual@tracedesk.test', role: 'regular', read_only: false, email_verified: true }
const summary = { observations: 2, services: ['checkout'], kinds: ['log'], input_lines: 2, input_bytes: 100, missing_timestamps: 0, missing_services: 0, duplicates: 0, malformed_records: 0, redactions: 0 }
const collection = { id: 'visual-collection', title: 'Checkout failure · UI fixture', symptom: 'An explicit analysis action.', created_at: '2026-11-07T10:00:00Z', source_type: 'paste', summary }
const report = { assessment: 'inconclusive', summary: 'A saved UI fixture assessment. No model was called.', origin: 'unknown', affected_services: [], evidence_ids: [], hypotheses: [], recommendations: ['Inspect saved source observations.'], limitations: ['Synthetic UI fixture.'], supporting_evidence: [] }
let loggedIn = false, readOnly = false, empty = false, state = 'completed', supplements = []
const investigation = () => ({ id: 'visual-report', collection_id: collection.id, engine: 'groq', state, created_at: collection.created_at, report: state === 'completed' ? report : null, activity: [], usage: { report_format: 'concise-v1' }, supplements })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
context.on('page', page => page.on('pageerror', error => errors.push(error.message)))
const handleApi = async route => {
  const request = route.request(), path = new URL(request.url()).pathname, method = request.method()
  requests.push({ path, method })
  let body, status = 200
  if (path === '/api/auth/me') { status = loggedIn ? 200 : 401; body = loggedIn ? { user: { ...user, read_only: readOnly, role: readOnly ? 'demo' : 'regular' }, csrf: 'visual-csrf' } : { error: 'Sign in' } }
  else if (path === '/api/auth/login') { loggedIn = true; body = { user, csrf: 'visual-csrf' } }
  else if (path === '/api/health') body = { demo_available: true, demo_case_count: 6 }
  else if (path === '/api/collections/visual-collection') body = { collection: { ...collection, summary: { ...summary, observations: empty ? 0 : 2 } }, investigations: [] }
  else if (path === '/api/collections/visual-collection/investigations') { await pause(600); body = { id: 'visual-report' } }
  else if (path === '/api/investigations/visual-report/supplements') { await pause(600); body = { id: 'supplement', investigation_id: 'visual-report', state: 'queued', created_at: collection.created_at, source_ids: [], usage: {}, result: null }; supplements = [body] }
  else if (path === '/api/investigations/visual-report/evidence') body = { investigation_id: 'visual-report', evidence: [], source_ids: [], unavailable_source_ids: [], coverage: 'recorded_supplied', note: 'UI fixture' }
  else if (path === '/api/investigations/visual-report') body = investigation()
  else { status = 404; body = { error: 'Unexpected UI fixture request: ' + path } }
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}
await context.route('**/api/**', handleApi)
const page = await context.newPage()
const writes = () => requests.filter(request => request.method === 'POST').length
async function check(name, run) {
  if (process.env.DESK_EFFECTS_ONLY && !new RegExp(process.env.DESK_EFFECTS_ONLY).test(name)) return
  await run(); results.push(name); console.log('PASS ' + name)
}
async function noOverflow() {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No horizontal scrolling')
}
async function capture(name, audit = false) {
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
  if (audit) {
    const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze()
    violations.push(...result.violations.map(item => ({ screen: name, id: item.id, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })))
  }
}
try {
  await check('pale wordmark responds to hover, fits all widths and stops offscreen', async () => {
    await page.goto(origin)
    await page.locator('.home-wordmark').scrollIntoViewIfNeeded()
    await page.locator('.home-wordmark canvas').waitFor()
    await page.evaluate(() => document.fonts.ready)
    await page.locator('.home-wordmark').hover()
    await page.mouse.move(0, 0)
    await pause(700)
    const before = await page.locator('.home-wordmark').screenshot()
    await page.locator('.home-wordmark').hover({ position: { x: 300, y: 150 } })
    await pause(250)
    const after = await page.locator('.home-wordmark').screenshot({ path: `${dir}/wordmark-hover.png` })
    assert(!before.equals(after), 'Hover changes the wordmark rendering')
    await page.mouse.move(0, 0)
    await pause(900)
    const restored = await page.locator('.home-wordmark').screenshot({ path: `${dir}/wordmark-restored.png` })
    assert(before.equals(restored), 'Leaving the text resets the reveal instead of leaving it behind')
    assert.equal(await page.locator('.wordmark-trace').count(), 0)
    assert.equal(await page.locator('.wordmark-live > div > div').count(), 0, 'Coordinate readouts are removed with the rig')
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await page.locator('.home-wordmark').scrollIntoViewIfNeeded()
      await noOverflow()
      await page.locator('.home-wordmark').screenshot({ path: `${dir}/wordmark-${width}.png` })
    }
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.waitForFunction(() => !document.querySelector('.home-wordmark canvas'))
    assert.equal(writes(), 0)
  })

  await check('login/signup artwork preserves the form and the desktop/mobile boundary', async () => {
    for (const mode of ['login', 'signup', 'demo-login']) {
      for (const width of [1920, 1440, 1024, 768, 761, 760, 390, 320]) {
        await page.setViewportSize({ width, height: 1000 })
        await page.goto(`${origin}/#/${mode}`)
        await page.locator('.auth-form-wrap').waitFor()
        if (width > 760) {
          await page.locator('.auth-artwork-live[data-painted]').waitFor()
          const composition = await page.locator('.auth-artwork').evaluate(art => {
            const parent = art.parentElement, box = art.getBoundingClientRect(), pb = parent.getBoundingClientRect()
            return {parent:parent.classList.contains('auth-page'), width:box.width, pageWidth:pb.width, mask:getComputedStyle(art).maskImage, pointer:getComputedStyle(art).pointerEvents, backgrounds:[...parent.querySelectorAll(':scope > section')].map(e=>getComputedStyle(e).backgroundColor)}
          })
          assert(composition.parent && composition.width === composition.pageWidth, 'Artwork spans the whole page')
          assert(composition.mask.includes('30%') && composition.mask.includes('70%'))
          assert.deepEqual(composition.backgrounds, ['rgba(0, 0, 0, 0)', 'rgba(0, 0, 0, 0)'])
          assert.equal(composition.pointer, 'none')
          if (mode !== 'demo-login') await page.getByLabel('Email address').fill('layout@tracedesk.test')
          else assert(await page.getByRole('button', {name:'Enter recorded demo'}).isEnabled())
        }
        else assert.equal(await page.locator('.auth-artwork canvas,.auth-artwork img').count(), 0)
        assert.equal(await page.locator('.auth-story .code-preview').count(), 0)
        assert(await page.locator('.auth-story .brand').isVisible())
        assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).scrollbarWidth), 'thin')
        assert((await page.evaluate(() => getComputedStyle(document.documentElement).scrollbarColor)).endsWith('rgba(0, 0, 0, 0)'), 'Auth scrollbar track stays transparent')
        if (width > 760) assert.equal(await page.locator('.auth-story').evaluate(element => getComputedStyle(element).borderRightWidth), '0px')
        await noOverflow()
        if ([1920, 1440, 1024, 768, 390, 320].includes(width)) await capture(`${mode}-${width}`, width === 1440 || width === 390)
      }
    }
    await page.goto(`${origin}/#/forgot`)
    assert.equal(await page.locator('.auth-artwork').count(), 0)
  })

  await check('auth tabs preserve the live renderer while resetting form fields', async () => {
    await page.setViewportSize({width: 1440, height: 700})
    await page.goto(`${origin}/#/login`)
    await page.locator('.auth-artwork-live[data-painted]').waitFor()
    await page.getByLabel('Email address').fill('reset@tracedesk.test')
    await page.evaluate(() => {
      window.authCanvas = document.querySelector('.auth-artwork-live canvas')
      window.authCanvasSize = [window.authCanvas.width, window.authCanvas.height]
    })
    for (const label of ['Create account', 'Demo', 'Sign in', 'Create account', 'Sign in']) {
      await page.locator('.auth-tabs').getByRole('link', {name:label, exact:true}).click()
      assert(await page.evaluate(() => window.authCanvas === document.querySelector('.auth-artwork-live canvas')), 'Auth tab must reuse the exact canvas')
      assert(await page.evaluate(() => window.authCanvas.width === window.authCanvasSize[0] && window.authCanvas.height === window.authCanvasSize[1]), 'Form height changes must not clear the drawing buffer')
      assert.equal(await page.locator('.auth-artwork-live').getAttribute('data-painted'), 'true')
      if (label !== 'Demo') assert.equal(await page.getByLabel('Email address').inputValue(), '')
    }
    await capture('auth-tabs-continuous')
    await page.evaluate(() => { delete window.authCanvas; delete window.authCanvasSize })
  })

  await check('auth switching keeps artwork painted even when signup adds a scrollbar', async () => {
    for (const height of [592, 520]) {
      await page.setViewportSize({ width: 1280, height })
      await page.goto(`${origin}/#/login`)
      await page.locator('.auth-artwork-live[data-painted]').waitFor()
      await page.evaluate(() => {
        const live = document.querySelector('.auth-artwork-live'), canvas = live.querySelector('canvas')
        window.artworkSwitch = { canvas, width: canvas.width, height: canvas.height, resets: [] }
        window.artworkSwitch.observer = new MutationObserver(records => {
          for (const record of records) {
            if (record.type === 'childList' || record.target === canvas || record.attributeName === 'data-painted') {
              window.artworkSwitch.resets.push(record.attributeName || 'renderer replaced')
            }
          }
        })
        window.artworkSwitch.observer.observe(live, { subtree: true, childList: true, attributes: true, attributeFilter: ['width', 'height', 'data-painted'] })
      })
      const before = await page.locator('.auth-artwork-live canvas').screenshot()
      const scrollbars = []
      for (const label of ['Create account', 'Demo', 'Sign in', 'Create account', 'Sign in']) {
        await page.locator('.auth-tabs').getByRole('link', { name: label, exact: true }).click()
        await pause(100)
        const state = await page.evaluate(() => {
          const live = document.querySelector('.auth-artwork-live'), canvas = live.querySelector('canvas'), initial = window.artworkSwitch
          return { same: canvas === initial.canvas, size: canvas.width === initial.width && canvas.height === initial.height,
            painted: live.dataset.painted, resets: initial.resets, scrolling: document.documentElement.scrollHeight > innerHeight }
        })
        assert(state.same && state.size && state.painted === 'true', 'Switching keeps the same painted canvas and dimensions')
        assert.deepEqual(state.resets, [], 'No transient clear or fallback flash while switching')
        scrollbars.push(state.scrolling)
        await noOverflow()
      }
      assert(!(await page.locator('.auth-artwork-live canvas').screenshot()).equals(before), 'Artwork continues to animate across tab switches')
      if (height === 592) assert(scrollbars.every(value => !value), 'All auth forms fit the supplied screenshot viewport')
      else assert(scrollbars.includes(true) && scrollbars.includes(false), 'Short-window case exercises scrollbar appearance and removal')
      await page.evaluate(() => { window.artworkSwitch.observer.disconnect(); delete window.artworkSwitch })
      await capture(`auth-switch-${height}`)
    }
  })

  await check('auth retains painted pixels through hidden tabs, delayed first paint, and resize', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${origin}/#/login`)
    await page.locator('.auth-artwork-live[data-painted]').waitFor()
    assert(await page.locator('.auth-artwork-poster').evaluate(img => img.complete && img.naturalWidth > 0))
    const luminance = async selector => page.locator(selector).evaluate(canvas => {
      const sample = document.createElement('canvas'); sample.width = 48; sample.height = 32
      const ctx = sample.getContext('2d'); ctx.drawImage(canvas, 0, 0, 48, 32)
      return [...ctx.getImageData(0, 0, 48, 32).data].filter((v, i) => i % 4 !== 3 && v > 40).length
    })
    for (let cycle = 0; cycle < 3; cycle++) {
      await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')) })
      await page.waitForFunction(() => !document.querySelector('.auth-artwork-live canvas'))
      const retained = await page.locator('.auth-artwork-retained').evaluate(c => c.toDataURL())
      assert(await luminance('.auth-artwork-retained') > 72, 'Hidden renderer leaves real artwork pixels')
      // Suppress actual draws in the new renderer to exercise a slow/failed first
      // paint. Black canvas/root must NEVER replace the retained artwork.
      await page.evaluate(() => {
        const proto = CanvasRenderingContext2D.prototype
        window.restoreArtworkDraw = {}
        for (const method of ['drawImage', 'fill', 'fillRect', 'stroke', 'strokeRect', 'putImageData']) {
          window.restoreArtworkDraw[method] = proto[method]
          proto[method] = function (...args) {
            if (this.canvas.closest?.('.auth-artwork-live')) return
            return window.restoreArtworkDraw[method].apply(this, args)
          }
        }
        delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))
      })
      await page.locator('.auth-artwork-live canvas').waitFor({ state: 'attached' })
      assert.equal(await page.locator('.auth-artwork-live').evaluate(e => getComputedStyle(e).opacity), '0')
      assert((await page.locator('.auth-artwork-retained').evaluate(c => c.toDataURL())) === retained, 'Retained frame stays unchanged before live paint')
      await capture(`tab-return-immediate-${cycle}`)
      assert.equal(await page.locator('.auth-artwork-live').getAttribute('data-painted'), null, 'No timer reveals an unpainted renderer')
      await page.evaluate(() => { Object.assign(CanvasRenderingContext2D.prototype, window.restoreArtworkDraw); delete window.restoreArtworkDraw })
      await page.locator('.auth-artwork-live[data-painted]').waitFor()
      assert(await luminance('.auth-artwork-live canvas') > 72)
    }
    await page.setViewportSize({ width: 1024, height: 900 })
    await page.locator('.auth-artwork-live[data-painted]').waitFor()
    assert(await luminance('.auth-artwork-live canvas') > 72)
    await capture('auth-resize-painted')
  })

  await check('reduced motion uses original static artwork and text without animation canvases', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto(`${origin}/#/login`)
    await page.locator('.auth-artwork img').waitFor()
    assert.equal(await page.locator('.auth-artwork-live canvas').count(), 0)
    await capture('login-reduced-motion')
    await page.goto(origin)
    await page.locator('.home-wordmark').scrollIntoViewIfNeeded()
    assert.equal(await page.locator('.home-wordmark canvas').count(), 0)
    assert(await page.locator('.wordmark-static').isVisible())
    await page.emulateMedia({ reducedMotion: 'no-preference' })
  })

  await check('login retains its deep link and the analysis button has native keyboard/disabled behavior', async () => {
    await page.goto(`${origin}/#/collections/visual-collection`)
    await page.getByLabel('Email address').fill(user.email)
    await page.getByLabel('Password', { exact: true }).fill('UI-fixture-password')
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
    const button = page.getByRole('button', { name: 'Investigate', exact: true })
    await button.waitFor()
    await page.locator('.spider-pill').waitFor()
    const before = writes()
    await button.hover()
    await pause(300)
    await capture('investigate-hover', true)
    const blackWebPixels = await page.locator('.analysis-action canvas').first().evaluate(canvas => {
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
      let count = 0
      for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] > 80 && pixels[i] < 10 && pixels[i + 1] < 10 && pixels[i + 2] < 10) count++
      return count
    })
    assert(blackWebPixels > 30, 'Lemon hover uses visible black web details')
    const box = await button.boundingBox()
    await page.mouse.move(box.x + box.width / 2, box.y - 55, { steps: 12 })
    await pause(450)
    const outsideWebPixels = await page.locator('.analysis-action canvas').first().evaluate(canvas => {
      const button = canvas.parentElement.querySelector('button'), cb = canvas.getBoundingClientRect(), bb = button.getBoundingClientRect()
      const rows = Math.max(1, Math.floor((bb.top - cb.top - 3) * canvas.height / cb.height))
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, rows).data
      let count = 0
      for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 80) count++
      return count
    })
    assert(outsideWebPixels > 20, 'Web strands extend beyond the pill toward the pointer')
    await capture('spider-web-reach')
    assert.equal(writes(), before, 'Hover must not submit analysis')
    await button.focus()
    assert.equal(writes(), before, 'Focus must not submit analysis')
    await page.keyboard.press('Enter')
    const pending = page.getByRole('button', { name: 'Submitting…', exact: true })
    await pending.waitFor()
    assert(await pending.isDisabled())
    await page.getByRole('button', { name: 'New investigation', exact: true }).waitFor()
    assert.equal(writes(), before + 1)
    await page.locator('.spider-pill').first().waitFor()
    await capture('report-actions', true)
    await noOverflow()
  })

  await check('further analysis and both retry labels keep their explicit request contracts', async () => {
    let before = writes()
    await page.getByRole('button', { name: 'Analyze further', exact: true }).click()
    await page.getByText('Reviewing the saved assessment…', { exact: true }).waitFor()
    assert.equal(writes(), before + 1)
    supplements = [{ ...supplements[0], state: 'failed', error: 'Fixture retry' }]
    await page.reload()
    const retry = page.getByRole('button', { name: 'Retry further analysis', exact: true })
    await retry.waitFor()
    before = writes()
    await retry.click()
    await page.getByText('Reviewing the saved assessment…', { exact: true }).waitFor()
    assert.equal(writes(), before + 1)
    state = 'failed'; supplements = []
    await page.reload()
    await page.getByRole('button', { name: 'Retry investigation', exact: true }).waitFor()
    await capture('retry-investigation')
    state = 'completed'
  })

  await check('empty collections stay disabled and read-only accounts have no analysis actions', async () => {
    empty = true
    await page.goto(`${origin}/#/collections/visual-collection`)
    const button = page.getByRole('button', { name: 'Investigate', exact: true })
    await button.waitFor()
    await page.locator('.spider-pill').waitFor()
    assert(await button.isDisabled())
    await capture('disabled-investigate')
    empty = false; readOnly = true
    await page.reload()
    await page.getByRole('heading', { name: collection.title, exact: true }).waitFor()
    assert.equal(await page.locator('.analysis-action').count(), 0)
    readOnly = false
  })

  await check('mobile reduced-motion controls stay native and remain usable', async () => {
    await page.setViewportSize({ width: 390, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.reload()
    const button = page.getByRole('button', { name: 'Investigate', exact: true })
    await button.waitFor()
    assert.equal(await page.locator('.analysis-action canvas').count(), 0)
    await capture('mobile-analysis', true)
    await noOverflow()
    await button.click()
    await page.getByRole('button', { name: 'New investigation', exact: true }).waitFor()
  })

  await check('a real touch-capable viewport gets a native analysis button without hover canvases', async () => {
    const touch = await browser.newContext({ viewport: { width: 390, height: 900 }, hasTouch: true, isMobile: true })
    await touch.route('**/api/**', handleApi)
    const surface = await touch.newPage()
    try {
      await surface.goto(`${origin}/#/collections/visual-collection`)
      const button = surface.getByRole('button', { name: 'Investigate', exact: true })
      await button.waitFor()
      assert.equal(await surface.locator('.analysis-action canvas').count(), 0)
      await surface.screenshot({ path: `${dir}/touch-analysis.png`, fullPage: true })
      const before = writes()
      await button.tap()
      await surface.getByRole('button', { name: 'New investigation', exact: true }).waitFor()
      assert.equal(writes(), before + 1)
    } finally { await touch.close() }
  })

  await check('hidden-tab events and route changes unmount live renderers', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.locator('.spider-pill').first().waitFor()
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')) })
    await page.waitForFunction(() => !document.querySelector('.analysis-action canvas'))
    await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')) })
    await page.locator('.spider-pill').first().waitFor()
    await page.goto(`${origin}/#/privacy`)
    assert.equal(await page.locator('.analysis-action canvas,.auth-artwork canvas,.home-wordmark canvas').count(), 0)
  })

  await check('WebGL loss and unavailable WebGL retain readable, usable fallbacks', async () => {
    await page.goto(origin)
    await page.locator('.home-wordmark').scrollIntoViewIfNeeded()
    await page.locator('.home-wordmark canvas').waitFor()
    await page.locator('.home-wordmark canvas').evaluate(canvas => canvas.dispatchEvent(new Event('webglcontextlost')))
    await page.waitForFunction(() => !document.querySelector('.home-wordmark canvas'))
    assert(await page.locator('.wordmark-static').isVisible())
    await page.addInitScript(() => {
      const getContext = HTMLCanvasElement.prototype.getContext
      HTMLCanvasElement.prototype.getContext = function (type, ...args) { return /^webgl/.test(type) ? null : getContext.call(this, type, ...args) }
    })
    await page.goto(`${origin}/#/collections/visual-collection`)
    await page.getByRole('button', { name: 'Investigate', exact: true }).waitFor()
    assert.equal(await page.locator('.analysis-action canvas').count(), 0)
    assert(await page.getByRole('button', { name: 'Investigate', exact: true }).isEnabled())
  })
  assert.deepEqual(errors, [])
  assert.deepEqual(violations, [])
} catch (error) {
  errors.push(error.stack); console.error(error); process.exitCode = 1
  await page.screenshot({ path: `${dir}/failure.png`, fullPage: true }).catch(() => {})
} finally {
  await writeFile(`${dir}/acceptance.json`, JSON.stringify({ results, errors, violations, requests, backend: 'Local mocked UI contracts only' }, null, 2))
  await browser.close()
  await new Promise(resolve => server.httpServer.close(resolve))
}
