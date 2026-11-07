import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { ArrowDown, ArrowRight, ArrowUpRight, Check, CircleDot, FileCheck2, FileText, GitBranch, Layers3, Pause, Play, Search, Terminal, TriangleAlert } from 'lucide-react'

type SampleObservation = { id: string; offset: string; service: 'gateway' | 'billing'; level: 'info' | 'error'; message: string }
// Synthetic and self-contained: homepage browsing never fetches evidence.
const observations: SampleObservation[] = [
  { id: '01', offset: '000', service: 'gateway', level: 'info', message: 'POST /checkout received' },
  { id: '02', offset: '020', service: 'gateway', level: 'info', message: 'Billing authorization requested' },
  { id: '03', offset: '034', service: 'billing', level: 'info', message: 'Authorization started' },
  { id: '04', offset: '042', service: 'billing', level: 'info', message: 'Reading invoice reference' },
  { id: '05', offset: '084', service: 'billing', level: 'error', message: 'Invoice reference is null' },
  { id: '06', offset: '104', service: 'gateway', level: 'error', message: 'Checkout returned HTTP 500' },
]
const steps = [
  { id: 'evidence', label: 'Select evidence', icon: Layers3, title: 'Start with what happened.', note: 'Six observations. One saved request.' },
  { id: 'request', label: 'Follow the request', icon: GitBranch, title: 'See how the failure connects.', note: 'Follow the recorded relationship from gateway to billing.' },
  { id: 'assessment', label: 'Read assessment', icon: FileCheck2, title: 'A finding you can inspect.', note: 'Supporting sources and unanswered questions, together.' },
  { id: 'source', label: 'Inspect source', icon: Search, title: 'Go straight to the source.', note: 'The exact observation behind the assessment.' },
]
const interval = 6000

function SampleLogs({ selectedId, complete = false }: { selectedId?: string; complete?: boolean }) {
  return <div className={'sample-log-list' + (complete ? ' is-complete' : '')} role="table" aria-label="Sample recorded observations">
    <div className="sample-log-head" role="row"><span role="columnheader">Time</span><span role="columnheader">Level</span><span role="columnheader">Observation</span></div>
    {observations.map(row => <div key={row.id} role="row" className={'sample-log-row' + (row.id === selectedId ? ' is-selected' : '') + (['02', '04'].includes(row.id) ? ' mobile-extra-row' : '')}>
      <span role="cell" className="sample-time">14:02:07.<b>{row.offset}</b></span>
      <span role="cell"><span className={'sample-level ' + row.level}>{row.level === 'error' && <TriangleAlert size={10} aria-hidden="true" />}{row.level}</span></span>
      <span role="cell" className="sample-message"><span>{row.message}</span><code>{row.service}</code></span>
    </div>)}
  </div>
}

function RequestScene() {
  return <div className="sample-request">
    <div className="sample-section-label"><GitBranch size={14} />RECORDED REQUEST<span>trace / checkout-01</span></div>
    <div className="sample-flow" role="img" aria-label="Gateway checkout called billing authorization. Billing recorded a null invoice reference; checkout returned HTTP 500.">
      <div className="sample-service"><Terminal size={20} /><span>gateway</span><strong>POST /checkout</strong><small>104 ms · HTTP 500</small></div>
      <div className="sample-connector"><span>calls</span><ArrowRight size={24} /><ArrowDown size={24} /></div>
      <div className="sample-service is-failure"><TriangleAlert size={20} /><span>billing</span><strong>Authorize invoice</strong><small>Null reference · error</small></div>
    </div>
    <div className="sample-span-ruler"><span>Elapsed time</span><span>0</span><span>52</span><span>104 ms</span></div>
    <div className="sample-span"><code>checkout</code><div><span style={{ width: '100%' }} /></div><span>104 ms</span></div>
    <div className="sample-span is-failure"><code>authorize</code><div><span style={{ marginLeft: '32.69%', width: '48.08%' }} /></div><span>50 ms</span></div>
    <p className="sample-scene-note">Recorded span relationships connect these services. Timing alone does not establish a cause.</p>
  </div>
}

function AssessmentScene({ onSource }: { onSource: (id: string) => void }) {
  return <div className="sample-assessment">
    <span className="sample-supported"><Check size={13} />Supported by sample evidence</span>
    <h3>Billing authorization encountered a null invoice reference.</h3>
    <p>The checkout request then returned an error in the same recorded trace.</p>
    <div className="sample-section-label">SUPPORTING EVIDENCE</div>
    <button className="sample-citation" onClick={() => onSource('05')}><span>01</span><span>Null reference at Billing.java:42<small>billing · observation 05</small></span><ArrowUpRight size={16} /></button>
    <button className="sample-citation" onClick={() => onSource('06')}><span>02</span><span>Checkout returned HTTP 500<small>gateway · observation 06 · same trace</small></span><ArrowUpRight size={16} /></button>
    <p className="sample-scene-note">Select a supporting source to inspect the retained observations.</p>
  </div>
}

function Inspector({ step, sourceId }: { step: number; sourceId: string }) {
  const source = observations.find(row => row.id === sourceId)!
  return <aside className="sample-inspector" aria-label="Sample context">
    <span className="sample-section-label">{['COLLECTION CONTEXT', 'SELECTED FAILURE', 'ASSESSMENT LIMITS', 'SELECTED SOURCE'][step]}</span>
    <h3>{['One request. Its context.', 'billing.authorize', 'Keep the unknowns visible.', source.message + '.'][step]}</h3>
    {step === 0 ? <><p>A saved window around a failed checkout, ready to inspect.</p><dl><div><dt>Source</dt><dd>Imported logs</dd></div><div><dt>Services</dt><dd>gateway, billing</dd></div><div><dt>Correlation</dt><dd>One recorded trace</dd></div><div><dt>Window</dt><dd>104 ms</dd></div></dl><div className="sample-callout"><Layers3 size={16} /><strong>Context stays attached</strong><p>Original messages, timestamps, and service names travel with the evidence.</p></div></>
      : step === 1 ? <><div className="sample-callout attention"><TriangleAlert size={16} /><strong>Observed failure</strong><p>Authorization encountered a null invoice reference before checkout returned HTTP 500.</p></div><dl><div><dt>Service</dt><dd>billing</dd></div><div><dt>Location</dt><dd>Billing.java:42</dd></div><div><dt>Trace</dt><dd>checkout-01</dd></div></dl><p className="sample-inspector-note">The reason the invoice was absent still needs investigation.</p></>
      : step === 2 ? <><div className="sample-callout attention"><CircleDot size={16} /><strong>Still unknown</strong><p>The evidence does not establish why the invoice was absent.</p></div><span className="sample-section-label">NEXT CHECKS</span><ol><li>Inspect invoice loading for this request.</li><li>Compare with a successful authorization.</li></ol><p className="sample-inspector-note">An assessment is a starting point. Check each claim against its sources.</p></>
      : <><span className="sample-level error"><TriangleAlert size={12} />error</span><dl><div><dt>Observation</dt><dd>{source.id}</dd></div><div><dt>Service</dt><dd>{source.service}</dd></div><div><dt>Location</dt><dd>{source.id === '05' ? 'Billing.java:42' : 'POST /checkout'}</dd></div><div><dt>Trace</dt><dd>checkout-01</dd></div></dl><div className="sample-callout"><FileText size={16} /><strong>Retained source</strong><p className="sample-source-message">{source.id === '05' ? 'NullPointerException: invoice is null' : 'Checkout returned HTTP 500'}</p><code>{source.id === '05' ? 'Billing.authorize (Billing.java:42)' : 'gateway · trace checkout-01'}</code></div></>}
  </aside>
}

export function FeatureCarousel() {
  const root = useRef<HTMLElement>(null)
  const sourcePanel = useRef<HTMLDivElement>(null), focusSource = useRef(false)
  const [sourceId, setSourceId] = useState('05')
  const [step, setStep] = useState(0), [playing, setPlaying] = useState(true)
  const [hovered, setHovered] = useState(false), [focused, setFocused] = useState(false)
  const [visible, setVisible] = useState(false), [hidden, setHidden] = useState(false)
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const paused = hovered || focused || !visible || hidden || reducedMotion
  const running = playing && !paused
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const motion = () => { setReducedMotion(media.matches); if (media.matches) setPlaying(false) }
    const visibility = () => setHidden(document.hidden)
    motion(); visibility()
    media.addEventListener('change', motion)
    document.addEventListener('visibilitychange', visibility)
    const observer = 'IntersectionObserver' in window ? new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0 }) : null
    if (root.current && observer) observer.observe(root.current)
    if (!observer) setVisible(true)
    return () => { media.removeEventListener('change', motion); document.removeEventListener('visibilitychange', visibility); observer?.disconnect() }
  }, [])
  useEffect(() => {
    if (!running) return
    const timer = window.setTimeout(() => {
      // Check live preferences too: a media/visibility event can arrive after the timer.
      if (document.hidden || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
      setStep(value => (value + 1) % steps.length); setSourceId('05')
    }, interval)
    return () => window.clearTimeout(timer)
  }, [running, step])
  useEffect(() => { if (focusSource.current) { sourcePanel.current?.focus({ preventScroll: true }); focusSource.current = false } }, [step])
  function select(value: number) { setStep(value); setSourceId('05'); setPlaying(false) }
  const current = steps[step]
  return <section ref={root} className="product-preview" aria-label="Illustrative workflow preview" aria-roledescription="carousel" data-step={current.id} data-autoplay={playing && !reducedMotion} data-paused={paused} style={{ '--walkthrough-duration': `${interval}ms` } as CSSProperties}
    onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocusCapture={() => setFocused(true)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false) }}>
    <div className="preview-window-bar"><span><Terminal size={15} /><strong>TraceDesk</strong><span className="preview-breadcrumb">/ saved evidence / checkout</span></span><span className="preview-sample-label">Illustrative walkthrough · Sample data</span></div>
    <div className="preview-workbench">
      <nav className="preview-navigation" aria-label="Preview steps"><span className="sample-section-label">INVESTIGATION</span>{steps.map(({ id, label, icon: Icon }, index) => <button key={id} className={step === index ? 'is-active' : ''} aria-pressed={step === index} onClick={() => select(index)}><Icon size={16} /><span>{label}</span></button>)}<div className="preview-services"><span className="sample-section-label">IN THIS COLLECTION</span><span><i />gateway</span><span><i />billing</span><span className="preview-saved-note"><Layers3 size={13} />Saved snapshot</span></div></nav>
      <div className="preview-main"><header className="preview-case-header"><div><span className="sample-level error"><TriangleAlert size={12} />500</span><h2>Checkout failure</h2></div><p>POST /checkout<span>trace / checkout-01</span></p><dl><div><dt>OBSERVATIONS</dt><dd>6</dd></div><div><dt>SERVICES</dt><dd>2</dd></div><div><dt>ERROR LOGS</dt><dd>2</dd></div><div><dt>DURATION</dt><dd>104 <small>ms</small></dd></div></dl></header>
        <div ref={sourcePanel} tabIndex={-1} className="preview-scene" data-scene={current.id} aria-label={current.label}>
          {steps.map((item, index) => <div key={item.id} className={'preview-scene-panel' + (step === index ? ' is-active' : '')} aria-hidden={step !== index} inert={step !== index}>
            {index === 0 || index === 3 ? <><div className="sample-section-label sample-log-label">{index === 3 ? `SOURCE ${sourceId} · WITH SURROUNDING CONTEXT` : 'RECORDED OBSERVATIONS'}<span>14:02:07</span></div><SampleLogs selectedId={index === 3 ? sourceId : undefined} /></> : index === 1 ? <RequestScene /> : <AssessmentScene onSource={id => { focusSource.current = true; select(3); setSourceId(id) }} />}
          </div>)}
        </div>
        <div className="preview-context-line"><GitBranch size={13} /><span className="preview-desktop-note">One trace connects these observations.</span><span className="preview-mobile-note">{step === 0 || step === 3 ? '4 of 6 logs · full sample in details' : 'Saved sample · not live'}</span><span>Saved · not live</span></div>
      </div>
      <div className="preview-inspectors">{steps.map((item, index) => <div key={item.id} className={'preview-inspector-panel' + (step === index ? ' is-active' : '')} aria-hidden={step !== index} inert={step !== index}><Inspector step={index} sourceId={sourceId} /></div>)}</div>
      <details className="preview-mobile-context" onToggle={event => { if (event.currentTarget.open) setPlaying(false) }}><summary>View {['collection context', 'failure details', 'assessment limits', 'source details'][step]}<ArrowDown size={14} /></summary><Inspector step={step} sourceId={sourceId} /><div className="preview-all-logs"><h3>All six sample observations</h3><SampleLogs complete selectedId={step === 3 ? sourceId : undefined} /></div></details>
    </div>
    <div className="preview-playback"><div className="preview-caption-stack" aria-live={playing ? 'off' : 'polite'}>{steps.map((item, index) => <div key={item.id} className={'preview-step-caption' + (step === index ? ' is-active' : '')} aria-hidden={step !== index}><span className="preview-step-count">0{index + 1}<span>/ 04</span></span><div><strong>{item.title}</strong><p>{item.note}</p></div></div>)}</div><button className="preview-play-button" disabled={reducedMotion} aria-label={playing && !reducedMotion ? 'Pause walkthrough' : 'Play walkthrough'} onClick={() => setPlaying(value => !value)}>{playing && !reducedMotion ? <Pause size={15} /> : <Play size={15} />}<span>{reducedMotion ? 'Manual preview' : playing ? 'Pause' : 'Play'}</span></button><div key={`${step}-${running}`} className="preview-progress" data-running={running} aria-hidden="true" /></div>
  </section>
}
