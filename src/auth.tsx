import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, ShieldCheck } from 'lucide-react'
import { api, userMessage, type Session } from './api'
import { PasswordField, AccountPage } from './account'
import { Brand, Notice, Spinner } from './components/common'
import { Button } from './components/ui/button'
import { AuthArtwork } from './components/brand-effects'
import { navigate } from './routes'
import { AuthEmailField, AuthNameField } from './components/auth-field'

type AuthProps = { mode?: string; onSession: (session: Session) => void }

export function Auth({ mode = 'login', onSession }: AuthProps) {
  if (mode === 'forgot') return <AccountPage onExit={() => navigate('login')} onRegister={() => navigate('signup')} />
  return <div className="auth-page auth-page-art">
    <AuthArtwork />
    <section className="auth-story auth-story-art"><Brand /></section>
    <AuthForm key={mode} mode={mode} onSession={onSession} />
  </div>
}

// Reset fields, pending requests and errors per mode without rebuilding the artwork.
function AuthForm({ mode = 'login', onSession }: AuthProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [demoState, setDemoState] = useState<{ demo_available: boolean; demo_case_count: number } | null>(null)
  const signup = mode === 'signup', demo = mode === 'demo-login'
  useEffect(() => { if (!demo) return; let alive = true; api<{ demo_available: boolean; demo_case_count: number }>('/health').then(value => { if (alive) setDemoState(value) }).catch(e => { if (alive) setError(userMessage(e)) }); return () => { alive = false } }, [demo])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    try { onSession(await api<Session>(demo ? '/auth/demo-login' : signup ? '/auth/signup' : '/auth/login', demo ? { password: 'Demo@login' } : Object.fromEntries(new FormData(event.currentTarget)))) }
    catch (e) { setError(userMessage(e)) } finally { setBusy(false) }
  }
  return <section className="auth-form-side"><a className="back-link" href="/"><ArrowLeft size={14} />Back to TraceDesk</a><div className="auth-form-wrap"><span className="eyebrow">{demo ? 'RECORDED DEMO' : signup ? 'YOUR WORKSPACE' : 'WELCOME BACK'}</span><h1>{demo ? 'Explore a real recording.' : signup ? 'Create your account.' : 'Sign in to TraceDesk.'}</h1><p>{demo ? 'Follow saved investigations and inspect their sources.' : signup ? 'Bring your evidence. Start an investigation when you are ready.' : 'Pick up where your investigation left off.'}</p><div className="auth-tabs"><a href="/login" aria-current={!signup && !demo ? 'page' : undefined}>Sign in</a><a href="/signup" aria-current={signup ? 'page' : undefined}>Create account</a><a href="/demo-login" aria-current={demo ? 'page' : undefined}>Demo</a></div>
      <form onSubmit={submit}>{signup && <AuthNameField name="name" autoComplete="name" required maxLength={60} disabled={busy} placeholder="Your name" />}{!demo && <><AuthEmailField name="email" autoComplete="email" required maxLength={200} disabled={busy} placeholder="you@example.com" /><PasswordField recessed label="Password" name="password" autoComplete={signup ? 'new-password' : 'current-password'} required minLength={signup ? 10 : 1} maxLength={128} disabled={busy} placeholder={signup ? 'At least 10 characters' : 'Your password'} /></>}{demo && <Notice>{demoState ? demoState.demo_available ? <>{demoState.demo_case_count} reviewed recordings. Read-only access. No analysis requests while browsing.</> : 'Recorded demos are unavailable. You can create an account and import your own logs.' : error ? 'Demo availability could not be checked.' : <><Spinner />Checking recorded demos…</>}</Notice>}{error && <Notice kind="error">{error}</Notice>}<Button className="full" aria-busy={busy || undefined} disabled={busy || (demo && !demoState?.demo_available)}><span className="inline-spinner">{busy && <Spinner />}</span>{demo ? 'Enter recorded demo' : signup ? 'Create account' : 'Sign in'}<ArrowRight size={16} /></Button></form>
      {!signup && !demo && <a className="forgot-link" href="/forgot">Forgot password?</a>}<p className="auth-privacy"><ShieldCheck size={16} />{demo ? 'Example data is clearly labelled throughout.' : 'Your saved evidence belongs to your account.'}</p><a className="text-link" href="/privacy">How evidence is processed<ArrowUpRightIcon /></a>
    </div></section>
}
function ArrowUpRightIcon() { return <span aria-hidden="true">↗</span> }
