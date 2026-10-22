import { useState, type FormEvent } from 'react';
import { ApiError, login, verifyMfa, type Me } from './api';
import { t } from './i18n';

export default function Login({ onLoggedIn }: { onLoggedIn: (me: Me) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<'password' | 'code'>('password');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  function fail(err: unknown) {
    const status = err instanceof ApiError ? err.status : 0;
    if (status === 429) setError(t.login.tooMany);
    else if (step === 'code' && status === 401) setError(t.login.invalidCode);
    else setError(status === 401 ? t.login.invalid : t.login.failed);
  }

  async function submitPassword(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const res = await login(email, password);
      if ('mfa_required' in res) setStep('code');
      else onLoggedIn(res);
    } catch (err) {
      fail(err);
    } finally {
      setPassword('');
      setBusy(false);
    }
  }

  async function submitCode(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      onLoggedIn(await verifyMfa(code));
    } catch (err) {
      fail(err);
      if (err instanceof ApiError && err.detail === 'not_authenticated') setStep('password');
    } finally {
      setCode('');
      setBusy(false);
    }
  }

  return (
    <main className="login">
      {step === 'password' ? (
        <form className="login-card" onSubmit={submitPassword} noValidate>
          <div className="brand">Heardloop</div>
          <h1>{t.login.title}</h1>
          <label>
            {t.login.email}
            <input type="email" autoComplete="username" dir="ltr" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            {t.login.password}
            <input type="password" autoComplete="current-password" dir="ltr" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" className="primary" disabled={busy || !email || !password}>
            {busy ? t.login.busy : t.login.submit}
          </button>
        </form>
      ) : (
        <form className="login-card" onSubmit={submitCode} noValidate>
          <div className="brand">Heardloop</div>
          <h1>{t.login.codeTitle}</h1>
          <p className="muted small">{t.login.codeHint}</p>
          <label>
            {t.login.code}
            <input
              autoComplete="one-time-code"
              inputMode="text"
              dir="ltr"
              autoFocus
              required
              maxLength={32}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" className="primary" disabled={busy || code.trim().length < 6}>
            {busy ? t.login.busy : t.login.verify}
          </button>
          <button type="button" className="link" onClick={() => { setStep('password'); setError(''); }}>
            {t.login.back}
          </button>
        </form>
      )}
    </main>
  );
}
