import { Fragment, lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Bell, Cable, ChevronRight, FileInput, Fingerprint, LayoutDashboard, Layers3, LogOut, Menu, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { api, ApiError, setCsrf, userMessage, type Session, type User } from './api'
import { AccountPage, parseAuthenticationLink, VerifyAccountPage, WritePermission } from './account'
import { Auth } from './auth'
import Home, { DataUse } from './home'
import { Brand, EmptyState, ErrorState, LoadingState } from './components/common'
import { useLoadingMotion } from './components/loading'
import { useRoute, useAppNavigation, navigate, navigateHref, emailDestination, currentRouteHref, navigationEvent, type SessionStatus } from './routes'

const Dashboard = lazy(() => import('./dashboard').then(module => ({ default: module.Dashboard })))
const Errors = lazy(() => import('./dashboard').then(module => ({ default: module.Errors })))
const Collections = lazy(() => import('./workspace').then(module => ({ default: module.Collections })))
const CollectionWorkspace = lazy(() => import('./workspace').then(module => ({ default: module.CollectionWorkspace })))
const Investigations = lazy(() => import('./workspace').then(module => ({ default: module.Investigations })))
const ConnectionsWorkspace = lazy(() => import('./connections').then(module => ({ default: module.ConnectionsWorkspace })))
const ImportWorkspace = lazy(() => import('./imports').then(module => ({ default: module.ImportWorkspace })))
const DemoWorkspace = lazy(() => import('./scenarios').then(module => ({ default: module.DemoWorkspace })))
const LogsWorkspace = lazy(() => import('./logs'))
const IncidentsWorkspace = lazy(() => import('./incidents').then(module => ({ default: module.IncidentsWorkspace })))
export { ReportView } from './assessments'

function clearDestination() { try { sessionStorage.removeItem('tracedesk:return-to') } catch { /* Storage can be disabled by the browser. */ } }
function pendingDestination() { try { return emailDestination(sessionStorage.getItem('tracedesk:return-to') || '') } catch { return '' } }

export default function App() {
  useLoadingMotion()
  useAppNavigation()
  const route = useRoute()
  const [user, setUser] = useState<User | null>(null), [sessionStatus, setSessionStatus] = useState<SessionStatus>('pending')
  const initializing = sessionStatus === 'pending'
  const [error, setError] = useState(''), [mobileNav, setMobileNav] = useState(false)
  const [authLink, setAuthLink] = useState(() => parseAuthenticationLink(window.location.hash))
  const [authLinkRevision, setAuthLinkRevision] = useState(0)
  const [invalidAuthLink, setInvalidAuthLink] = useState(() => window.location.hash.startsWith('#auth/') && !parseAuthenticationLink(window.location.hash))
  const epoch = useRef(0), main = useRef<HTMLElement>(null), sidebar = useRef<HTMLElement>(null), lastPage = useRef(route.page)

  const acceptSession = useCallback((value: Session, explicit = true) => {
    const destination = value.user.read_only ? '' : pendingDestination() || emailDestination(currentRouteHref())
    epoch.current++; setCsrf(value.csrf); setUser(value.user); setError(''); setSessionStatus('authenticated')
    if (value.user.email_verified || value.user.read_only) clearDestination()
    if (explicit) navigateHref(destination || '/dashboard', true)
  }, [])
  const refreshSession = useCallback(async () => {
    const revision = epoch.current
    try {
      const value = await api<Session>('/auth/me')
      if (revision !== epoch.current) return
      const destination = pendingDestination()
      acceptSession(value, false)
      if (value.user.email_verified && user && !user.email_verified && !user.read_only) navigateHref(destination || '/dashboard', true)
    } catch (error) { if (revision === epoch.current) throw error }
  }, [acceptSession, user])
  const expireSession = useCallback(() => {
    const destination = emailDestination(currentRouteHref())
    if (destination) try { sessionStorage.setItem('tracedesk:return-to', destination) } catch { /* Optional browser storage. */ }
    epoch.current++; setUser(null); setCsrf(''); setSessionStatus('anonymous'); setError('Your session expired. Sign in to open your dashboard.')
  }, [])
  const retrySession = useCallback(async () => {
    const revision = ++epoch.current
    setSessionStatus('pending'); setError('')
    try {
      const value = await api<Session>('/auth/me')
      if (revision === epoch.current) acceptSession(value, false)
    } catch (e) {
      if (revision !== epoch.current) return
      setUser(null); setCsrf('')
      const anonymous = e instanceof ApiError && e.status === 401
      setSessionStatus(anonymous ? 'anonymous' : 'error')
      if (!anonymous) setError(userMessage(e))
    }
  }, [acceptSession])
  useEffect(() => {
    void retrySession()
    window.addEventListener('tracedesk:session-expired', expireSession)
    return () => { epoch.current++; window.removeEventListener('tracedesk:session-expired', expireSession) }
  }, [retrySession, expireSession])
  useEffect(() => {
    function consume() {
      if (!window.location.hash.startsWith('#auth/')) return
      const link = parseAuthenticationLink(window.location.hash)
      setAuthLink(link); setInvalidAuthLink(!link); setAuthLinkRevision(value => value + 1)
      // The one-time token lives only in component state after the link is opened.
      navigate('account', '', {}, true)
    }
    consume()
    for (const event of ['hashchange', 'popstate', navigationEvent]) window.addEventListener(event, consume)
    return () => { for (const event of ['hashchange', 'popstate', navigationEvent]) window.removeEventListener(event, consume) }
  }, [])
  useEffect(() => {
    if (sessionStatus === 'anonymous' && ['', 'privacy'].includes(route.page)) clearDestination()
    if (sessionStatus === 'anonymous' || user && !user.email_verified && !user.read_only) {
      const destination = emailDestination(currentRouteHref())
      if (destination) try { sessionStorage.setItem('tracedesk:return-to', destination) } catch { /* Optional browser storage. */ }
    }
  }, [sessionStatus, user, route.page, route.id, route.params.toString()])
  useEffect(() => {
    if (user && ['login', 'signup', 'demo-login'].includes(route.page)) {
      navigate('dashboard', '', {}, true)
    }
    setMobileNav(false)
    if (lastPage.current !== route.page) { main.current?.focus(); window.scrollTo(0, 0); lastPage.current = route.page }
  }, [route.page, route.id, route.params.toString(), user, initializing])
  useEffect(() => {
    if (!user || user.email_verified || user.read_only) return
    const refresh = () => { void refreshSession().catch(e => { if (e instanceof ApiError && e.status === 401) expireSession(); else setError(userMessage(e)) }) }
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [user, refreshSession, expireSession])
  useEffect(() => {
    document.title = route.page === 'logs' ? 'Logs · TraceDesk' : route.page && route.page !== 'auth-link' ? route.page[0].toUpperCase() + route.page.slice(1) + ' · TraceDesk' : 'TraceDesk · Follow the evidence'
  }, [route.page])
  useEffect(() => {
    if (!mobileNav) return
    const previous = document.activeElement as HTMLElement | null, overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    sidebar.current?.querySelector<HTMLElement>('a,button')?.focus()
    return () => { document.body.style.overflow = overflow; previous?.focus() }
  }, [mobileNav])
  async function logout() {
    try { await api('/auth/logout', {}); epoch.current++; setUser(null); setCsrf(''); setSessionStatus('anonymous'); clearDestination(); navigate('login'); setError('') }
    catch (e) { if (e instanceof ApiError && e.status === 401) { expireSession(); navigate('login') } else setError(userMessage(e)) }
  }
  const exitAccount = () => { setAuthLink(null); setInvalidAuthLink(false); navigate('login') }
  if (authLink || invalidAuthLink) return <AccountPage key={`${authLink?.purpose || 'invalid'}:${authLink?.token || ''}:${authLinkRevision}`} link={authLink || undefined} invalid={invalidAuthLink} onExit={exitAccount} onReset={() => { epoch.current++; setUser(null); setCsrf(''); setSessionStatus('anonymous') }} onVerified={() => {
    void api<Session>('/auth/me').then(value => acceptSession(value)).catch(() => { epoch.current++; setUser(null); setCsrf(''); setSessionStatus('anonymous'); navigate('login') }).finally(() => setAuthLink(null))
  }} />
  if (route.page === 'privacy') return <div className="public-detail"><Brand /><DataUse /><a className="text-link" href={user ? '/dashboard' : '/'}>Return to {user ? 'workspace' : 'TraceDesk'}</a></div>
  if (!route.page) return <Home user={user} sessionStatus={sessionStatus} error={error} onRetry={() => void retrySession()} />
  if (initializing) return <div className="initializing"><Brand /><LoadingState cup>Opening your workspace…</LoadingState></div>
  if (sessionStatus === 'error') return <div className="initializing"><Brand /><ErrorState error={error} retry={() => void retrySession()} /></div>
  if (route.page === 'account') return <AccountPage invalid onExit={exitAccount} />
  if (route.page === 'not-found') return <div className="public-detail"><Brand /><EmptyState title="This page is unavailable"><a href="/">Return to TraceDesk</a></EmptyState></div>
  if (!user) return <>{error && <ErrorState error={error} />}<Auth mode={route.page === 'demo' ? 'demo-login' : route.page} onSession={acceptSession} /></>
  if (!user.read_only && !user.email_verified) return <VerifyAccountPage user={user} onRefresh={refreshSession} onLogout={logout} error={error} />

  const nav = [
    { page: 'dashboard', label: 'Dashboard', icon: LayoutDashboard }, { page: 'collections', label: 'Saved evidence', icon: Layers3 },
    { page: 'errors', label: 'Errors', icon: TriangleAlert }, { page: 'investigations', label: 'Investigations', icon: Fingerprint },
    ...(!user.read_only ? [{ page: 'incidents', label: 'Incidents', icon: Bell }, { page: 'connections', label: 'Connections', icon: Cable }, { page: 'import', label: 'Import logs', icon: FileInput }] : []),
  ]
  const title = route.page === 'logs' ? 'Log workspace' : route.page === 'demo' ? 'Recorded demos' : nav.find(item => item.page === route.page)?.label || 'Workspace'
  let content
  switch (route.page) {
    case 'dashboard': content = <Dashboard readOnly={user.read_only} />; break
    case 'collections': content = route.id ? <CollectionWorkspace key={route.id} id={route.id} readOnly={user.read_only} /> : <Collections readOnly={user.read_only} />; break
    case 'errors': content = <Errors />; break
    case 'investigations': content = <Investigations key={route.id} id={route.id} readOnly={user.read_only} />; break
    case 'logs': content = <LogsWorkspace />; break
    case 'demo': content = <DemoWorkspace slug={route.id} />; break
    case 'connections': content = user.read_only ? <EmptyState title="Read-only demonstration">Connections are available in a regular account.</EmptyState> : <ConnectionsWorkspace />; break
    case 'incidents': content = user.read_only ? <EmptyState title="Read-only demonstration">Automatic incidents belong to regular accounts.</EmptyState> : <IncidentsWorkspace key={route.id} id={route.id} />; break
    case 'import': content = user.read_only ? <EmptyState title="Read-only demonstration">Import your own evidence using a regular account.</EmptyState> : <ImportWorkspace />; break
    default: content = <EmptyState title="This page is unavailable"><a href="/dashboard">Return to your dashboard</a> to open saved evidence.</EmptyState>
  }
  return <WritePermission.Provider value={user.email_verified && !user.read_only}><div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus() }}>Skip to content</a>
    {mobileNav && <button className="nav-backdrop" tabIndex={-1} aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
    <aside ref={sidebar} id="workspace-navigation" role={mobileNav ? 'dialog' : undefined} aria-modal={mobileNav || undefined} aria-label="Workspace navigation" className={'sidebar' + (mobileNav ? ' nav-open' : '')} onKeyDown={event => {
      if (!mobileNav) return
      if (event.key === 'Escape') { event.preventDefault(); setMobileNav(false) }
      if (event.key === 'Tab') {
        const targets = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('a,button:not(:disabled)') || []).filter(element => element.offsetParent !== null)
        const first = targets[0], last = targets.at(-1)
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }}>
      <div className="sidebar-brand"><Brand /><button className="icon-button mobile-only" aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
      <div className="sidebar-scroll">
      <div className="workspace-label"><span className="workspace-avatar">{user.read_only ? 'D' : user.name.charAt(0).toUpperCase()}</span><div><strong>{user.read_only ? 'Demo workspace' : 'Personal workspace'}</strong><span>{user.read_only ? 'Recorded · Read-only' : 'Your saved evidence'}</span></div></div>
      <span className="nav-label">EXPLORER</span><nav aria-label="Workspace">{nav.map(({ page, label, icon: Icon }) => <Fragment key={page}>{page === 'connections' && <span className="nav-label nav-section-label">SOURCES</span>}<a href={'/' + page} aria-current={route.page === page || (page === 'collections' && route.page === 'logs') ? 'page' : undefined}><Icon size={18} />{label}</a></Fragment>)}</nav>
      {user.read_only && <a className={'demo-nav' + (route.page === 'demo' ? ' active' : '')} href="/demo"><Layers3 size={17} />Recorded demo gallery</a>}
      <div className="snapshot-note"><ShieldCheck size={16} /><p>{user.read_only ? 'Reviewed recordings. Browsing does not start analysis.' : 'Saved snapshots. Investigate when you are ready.'}</p></div></div>
      <div className="sidebar-bottom"><div className="user-row"><span className="avatar">{user.name.charAt(0).toUpperCase()}</span><div><strong>{user.name}</strong><span>{user.read_only ? 'Recorded demo account' : user.email}</span></div><button className="icon-button" aria-label="Sign out" onClick={() => void logout()}><LogOut size={16} /></button></div></div>
    </aside><div className="main-shell" inert={mobileNav}><header className="topbar"><div><button className="icon-button mobile-only" aria-label="Open navigation" aria-expanded={mobileNav} aria-controls="workspace-navigation" onClick={() => setMobileNav(!mobileNav)}><Menu size={20} /></button><span>Workspace</span><ChevronRight size={13} /><strong>{title}</strong></div><span className="connection-pill"><i />{user.read_only ? 'Recorded demonstration' : 'Saved evidence'}</span></header>
      <main ref={main} id="main-content" tabIndex={-1} key={user.id}>{error && <ErrorState error={error} />}<Suspense fallback={<LoadingState layout={route.page === 'dashboard' ? 'dashboard' : route.page === 'errors' ? 'errors' : route.page === 'collections' ? route.id ? 'collection' : 'cards' : route.page === 'demo' ? 'demo' : route.page === 'investigations' && !route.id ? 'investigations' : 'detail'} />}>{content}</Suspense><footer className="page-footer"><span>saved evidence / cited assessments</span><a href="/privacy">Data use</a></footer></main>
    </div></div></WritePermission.Provider>
}
