import { ArrowRight, ArrowUpRight, Cable, FileCheck2, FileText, GitBranch, History, Layers3, LockKeyhole, Menu, Plus, Search, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useRef } from 'react'
import type { User } from './api'
import type { SessionStatus } from './routes'
import { Brand, Spinner } from './components/common'
import { FooterWordmark } from './components/brand-effects'
import { Button } from './components/ui/button'
import { FeatureCarousel } from './components/ui/feature-carousel'

function scrollToSection(id: string) {
  const target = document.getElementById(id)
  target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' })
  target?.focus({ preventScroll: true })
}
export function WorkflowPreview() { return <FeatureCarousel /> }

export function DataUse() {
  return <section className="data-use" id="data-use"><LockKeyhole size={18} /><div>
    <h2>Data use & processing</h2>
    <p>Saved observations and reports belong to your account. Common credential patterns are redacted; review your evidence before investigating. An investigation sends selected excerpts to external AI providers. Browsing, importing and opening reports do not start analysis.</p>
    <details><summary>Provider and processing details</summary><p>TraceDesk uses TypeSafe Jev through OpenRouter to classify evidence, and Groq to assess it and write a report. Provider credentials remain on the server. Reports may be inconclusive and should be checked against their sources.</p></details>
  </div></section>
}

const features = [
  { icon: Layers3, label: 'COLLECT', title: 'Keep the context together.', text: 'Save logs as an evidence collection. Original messages, service names, and available timestamps stay close.', detail: 'Your evidence, retained' },
  { icon: Search, label: 'SEARCH', title: 'Find the line that matters.', text: 'Search observations and narrow by service, severity, time, or recorded request. Inspect the surrounding context.', detail: 'From the collection to the detail' },
  { icon: GitBranch, label: 'CONNECT', title: 'Follow the recorded request.', text: 'See how a request crossed services using recorded trace and span relationships. Missing links stay visible.', detail: 'Relationships grounded in evidence' },
  { icon: History, label: 'RECONSTRUCT', title: 'Put events in order.', text: 'Read a chronological timeline of your saved observations. See what happened before and after the failure.', detail: 'A timeline you can inspect' },
  { icon: FileCheck2, label: 'ASSESS', title: 'Answers with a way back.', text: 'Read a concise assessment with supporting citations, next checks, and clearly stated unknowns.', detail: 'Every finding stays close to its sources' },
  { icon: Cable, label: 'IMPORT', title: 'Start where your logs are.', text: 'Paste logs, upload a supported file, or connect Sentry to import a snapshot. Investigate when you are ready.', detail: 'An explicit next step, every time' },
]
const questions = [
  ['What can I bring into TraceDesk?', 'Paste application logs or import a UTF-8 .log, .txt or .jsonl file. TraceDesk saves the observations as a collection so you can review the available context before investigating.'],
  ['Do I need a Sentry connection?', 'No. Files and pasted logs work independently. Sentry is an optional way to import saved evidence windows into your account.'],
  ['Is this live monitoring?', 'The dashboard describes saved snapshots. Live infrastructure monitoring is not connected. Missing timestamps, traces and metrics remain visible as coverage gaps.'],
  ['Will every investigation find a root cause?', 'No. A report is supported or inconclusive based on the supplied evidence. It separates the observed failure mechanism from unknown deeper causes.'],
  ['What can I do in the recorded demo?', 'Browse reviewed recordings, their saved reports, and supporting evidence. Demo access is read-only and makes no analysis requests.'],
  ['How is my evidence processed?', 'An explicit investigation sends selected excerpts to external AI providers. Common credential patterns are redacted, but you should review the evidence first. Browsing, importing, and opening reports do not start analysis.'],
]

export default function Home({ user, sessionStatus, error, onRetry }: { user: User | null; sessionStatus: SessionStatus; error?: string; onRetry: () => void }) {
  const menu = useRef<HTMLDetailsElement>(null)
  const signedIn = sessionStatus === 'authenticated' && !!user
  const unverified = signedIn && !user.email_verified && !user.read_only
  function action(guestLabel: string, guestPage: string, secondary = false, style = 'text-link') {
    if (sessionStatus === 'error') return null
    if (sessionStatus === 'pending') return <span className={`home-action-placeholder ${style}`} aria-hidden="true" />
    const label = signedIn ? secondary ? unverified ? 'Verify your email' : user.read_only ? 'Browse recorded demos' : 'View saved evidence' : 'Open Dashboard' : guestLabel
    const href = signedIn ? secondary && !unverified ? user.read_only ? '/demo' : '/collections' : '/dashboard' : '/' + guestPage
    const link = <a className={style === 'primary' ? undefined : style} href={href}>{label}{style === 'primary' ? <ArrowRight size={15} /> : <ArrowUpRight size={14} />}</a>
    return style === 'primary' ? <Button asChild>{link}</Button> : link
  }
  function section(label: string) {
    return <button key={label} onClick={() => { if (menu.current) menu.current.open = false; scrollToSection(label.toLowerCase()) }}>{label}</button>
  }
  function menuAccount() {
    if (sessionStatus === 'pending') return <span className="home-session-status"><Spinner />Checking your session…</span>
    if (sessionStatus === 'error') return null
    return signedIn ? <a href="/dashboard">Dashboard<ArrowUpRight size={14} /></a> : <><a href="/login">Sign in</a><a href="/signup">Create account<ArrowUpRight size={14} /></a></>
  }
  return <div className="home-surface"><div className="home-page" data-session-status={sessionStatus}>
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); scrollToSection('main-content') }}>Skip to content</a>
    <header className="home-nav"><Brand /><nav className="home-section-nav" aria-label="Homepage sections">{['Features', 'Workflow', 'FAQ'].map(section)}</nav><details ref={menu} className="home-mobile-menu" onKeyDown={event => { if (event.key === 'Escape' && menu.current) { menu.current.open = false; menu.current.querySelector('summary')?.focus() } }}><summary aria-label="Homepage menu"><Menu size={18} /></summary><nav aria-label="Mobile homepage sections">{['Features', 'Workflow', 'FAQ'].map(section)}{sessionStatus !== 'error' && <div className="home-menu-account">{menuAccount()}</div>}</nav></details><nav className="home-account-nav" aria-label={signedIn ? 'Account navigation' : 'Public navigation'}>{sessionStatus === 'pending' ? <span className="home-session-status"><Spinner />Checking your session…</span> : sessionStatus === 'error' ? null : signedIn ? <Button asChild><a href="/dashboard">Dashboard<ArrowUpRight size={14} /></a></Button> : <><a href="/login">Sign in</a><Button asChild><a href="/signup">Create account<ArrowUpRight size={14} /></a></Button></>}</nav></header>
    <main id="main-content" tabIndex={-1}>
      <section className="hero"><div className="hero-copy"><span className="eyebrow"><span className="home-signal" />FOLLOW THE EVIDENCE</span><h1>Every failure<br /><span>leaves a trail.</span></h1></div><div className="hero-introduction"><p>Bring your application logs together. Follow the failure, inspect the assessment, and open the evidence behind it.</p>
        {sessionStatus === 'error' && <div className="home-session-feedback home-session-error" role="alert"><TriangleAlert size={18} aria-hidden="true" /><div><strong>Your session couldn’t be checked.</strong><p>Retry to load your account actions.</p><Button variant="secondary" onClick={onRetry}>Try again<ArrowRight size={14} /></Button></div></div>}
        {unverified && <div className="home-session-feedback" role="status"><ShieldCheck size={18} aria-hidden="true" /><p>Verify your email to open your workspace. Dashboard will guide you through verification.</p></div>}
        {sessionStatus === 'anonymous' && error && <p className="home-session-feedback" role="status">{error}</p>}
        {sessionStatus !== 'error' && <div className="hero-actions">{action('Create workspace', 'signup', false, 'primary')}{action('Open recorded demo', 'demo-login', true, 'button ghost')}{sessionStatus === 'pending' && <span className="home-session-status home-hero-session" role="status"><Spinner />Checking your session…</span>}</div>}
      </div></section>
      <WorkflowPreview />
      <div className="source-strip"><span>START WITH YOUR EVIDENCE</span><span><FileText size={15} />Log files</span><span><Layers3 size={15} />Pasted logs</span><span><Cable size={15} />Sentry snapshots</span><span className="source-strip-note">Your logs. Your service names.</span></div>
      <section className="home-features" id="features" tabIndex={-1} aria-labelledby="features-title"><div className="home-section-heading"><div><span className="eyebrow">01 / THE WORKSPACE</span><h2 id="features-title">Less searching.<br />More understanding.</h2></div><p>From the first error to the evidence behind it. The tools to collect, connect, and inspect a failure, together in one workspace.</p></div><div className="home-feature-grid">{features.map(({ icon: Icon, label, title, text, detail }) => <article key={label}><span className="home-feature-label"><Icon size={17} />{label}</span><h3>{title}</h3><p>{text}</p><span className="home-feature-detail"><ArrowUpRight size={13} />{detail}</span></article>)}</div></section>
      <section className="home-workflow" id="workflow" tabIndex={-1} aria-labelledby="workflow-title"><div className="home-section-heading"><div><span className="eyebrow">02 / HOW IT WORKS</span><h2 id="workflow-title">A shorter path<br />through the logs.</h2></div>{action('Inspect a recorded investigation', 'demo-login', true)}</div><div className="workflow-columns">{[
        ['01', 'Collect', 'Import logs or a Sentry snapshot. Review source coverage before you investigate.'],
        ['02', 'Investigate', 'Read the assessed mechanism, supporting observations, and what remains unknown.'],
        ['03', 'Inspect', 'Follow citations into searchable logs, request flow, and a chronological timeline.'],
      ].map(([number, title, text]) => <article key={number}><span className="workflow-number">{number}<ArrowRight size={18} /></span><h3>{title}</h3><p>{text}</p></article>)}</div></section>
      <section className="home-faq" id="faq" tabIndex={-1} aria-labelledby="faq-title"><div><span className="eyebrow">03 / BEFORE YOU START</span><h2 id="faq-title">A little clarity.<br />Before you dive in.</h2><p>Saved evidence. Explicit investigation. Results you can inspect.</p>{action('Explore the recorded demo', 'demo-login', true)}</div><div className="home-questions">{questions.map(([question, answer], index) => <details key={question} open={index === 0}><summary>{question}<Plus size={16} aria-hidden="true" /></summary><p>{answer}</p></details>)}</div></section>
      <DataUse />
      <section className="home-cta"><div><span className="eyebrow">YOUR NEXT INVESTIGATION</span><h2>Bring a failure<br /><span>worth investigating.</span></h2></div><div>{action('Create account', 'signup', false, 'primary')}{action('Start with a recorded demo', 'demo-login', true)}</div></section>
    </main>
    <footer className="home-footer"><div className="home-footer-top"><div className="home-footer-about"><Brand /><p>Follow the failure.<br />Keep the evidence close.</p><span><Layers3 size={13} />Built around saved evidence</span></div><nav aria-label="Explore"><span className="eyebrow">EXPLORE</span><button onClick={() => scrollToSection('features')}>Features</button><button onClick={() => scrollToSection('workflow')}>Workflow</button>{action('Recorded demo', 'demo-login', true)}</nav><nav aria-label="Account"><span className="eyebrow">ACCOUNT</span>{action('Create account', 'signup')}{action('Sign in', 'login', signedIn)}</nav><nav aria-label="Resources"><span className="eyebrow">RESOURCES</span><button onClick={() => scrollToSection('faq')}>FAQ</button><a href="/privacy">Data use<ArrowUpRight size={12} /></a></nav></div><div className="home-footer-bottom"><span>TraceDesk · Evidence-led investigation.</span><span>From what happened to what comes next.</span></div><FooterWordmark /></footer>
  </div></div>
}
