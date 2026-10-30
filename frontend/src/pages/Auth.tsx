import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { post } from '../api';
import { useAuth } from '../auth';
import { Message } from '../components/ui';

function AuthFrame({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="auth">
      <header className="auth-cover">
        <p className="auth-kicker">Calourada</p>
        <h1 className="auth-logo">Álbum de figurinhas</h1>
      </header>
      <section className="auth-card paper">
        <h2>{title}</h2>
        {children}
        {footer ? <div className="auth-footer">{footer}</div> : null}
      </section>
    </div>
  );
}

function useSubmit(action: () => Promise<void>) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { error, busy, onSubmit };
}

function Field(props: { label: string; value: string; onChange: (v: string) => void; type?: string; autoComplete?: string; minLength?: number; maxLength?: number; hint?: string }) {
  const { label, onChange, hint, ...rest } = props;
  return (
    <label className="field">
      <span>{label}</span>
      <input required {...rest} onChange={(event) => onChange(event.target.value)} />
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function LoginPage() {
  const { user, refresh } = useAuth();
  const location = useLocation();
  const next = (location.state as { next?: string } | null)?.next ?? '/';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const { error, busy, onSubmit } = useSubmit(async () => {
    await post('/auth/login', { email, password });
    await refresh();
  });
  if (user) return <Navigate to={next} replace />;
  return (
    <AuthFrame
      title="Entrar"
      footer={
        <>
          <Link to="/cadastro" state={{ next }}>Criar conta</Link>
          <Link to="/esqueci">Esqueci a senha</Link>
        </>
      }
    >
      <form onSubmit={onSubmit}>
        <Field label="E-mail" type="email" autoComplete="email" value={email} onChange={setEmail} />
        <Field label="Senha" type="password" autoComplete="current-password" value={password} onChange={setPassword} />
        <Message>{error}</Message>
        <button className="btn btn-primary" disabled={busy}>{busy ? 'Entrando…' : 'Entrar'}</button>
      </form>
    </AuthFrame>
  );
}

export function RegisterPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [nickname, setNickname] = useState('');
  const [done, setDone] = useState('');
  const { error, busy, onSubmit } = useSubmit(async () => {
    const result = await post('/auth/register', { email, password, nickname });
    setDone(result.message);
  });
  if (done) {
    return (
      <AuthFrame title="Quase lá" footer={<Link to="/entrar">Voltar para entrar</Link>}>
        <Message kind="ok">{done}</Message>
      </AuthFrame>
    );
  }
  return (
    <AuthFrame title="Criar conta" footer={<Link to="/entrar">Já tenho conta</Link>}>
      <form onSubmit={onSubmit}>
        <Field label="E-mail" type="email" autoComplete="email" value={email} onChange={setEmail} hint="Ninguém mais vê seu e-mail." />
        <Field label="Apelido" autoComplete="nickname" minLength={3} maxLength={20} value={nickname} onChange={setNickname} hint="É o nome que aparece nas trocas." />
        <Field label="Senha" type="password" autoComplete="new-password" minLength={8} value={password} onChange={setPassword} hint="Pelo menos 8 caracteres." />
        <Message>{error}</Message>
        <button className="btn btn-primary" disabled={busy}>{busy ? 'Criando…' : 'Criar conta'}</button>
      </form>
    </AuthFrame>
  );
}

export function ConfirmPage() {
  const [params] = useSearchParams();
  const { refresh } = useAuth();
  const [state, setState] = useState<{ ok?: string; error?: string }>({});
  const sent = useRef(false);
  useEffect(() => {
    if (sent.current) return;
    sent.current = true;
    post('/auth/confirm', { token: params.get('token') ?? '' })
      .then((result) => {
        setState({ ok: result.message });
        return refresh();
      })
      .catch((error) => setState({ error: error.message }));
  }, [params, refresh]);
  return (
    <AuthFrame title="Confirmação de e-mail" footer={<Link to="/">Ir para o álbum</Link>}>
      {state.ok ? <Message kind="ok">{state.ok} Seus pacotes já estão esperando.</Message> : null}
      {state.error ? <Message>{state.error}</Message> : null}
      {!state.ok && !state.error ? <p>Confirmando…</p> : null}
    </AuthFrame>
  );
}

export function ForgotPage() {
  const [email, setEmail] = useState('');
  const [done, setDone] = useState('');
  const { error, busy, onSubmit } = useSubmit(async () => {
    const result = await post('/auth/forgot', { email });
    setDone(result.message);
  });
  return (
    <AuthFrame title="Recuperar senha" footer={<Link to="/entrar">Voltar para entrar</Link>}>
      {done ? (
        <Message kind="ok">{done}</Message>
      ) : (
        <form onSubmit={onSubmit}>
          <Field label="E-mail" type="email" autoComplete="email" value={email} onChange={setEmail} />
          <Message>{error}</Message>
          <button className="btn btn-primary" disabled={busy}>Enviar link</button>
        </form>
      )}
    </AuthFrame>
  );
}

export function ResetPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const { error, busy, onSubmit } = useSubmit(async () => {
    await post('/auth/reset', { token: params.get('token') ?? '', password });
    navigate('/entrar');
  });
  return (
    <AuthFrame title="Nova senha">
      <form onSubmit={onSubmit}>
        <Field label="Nova senha" type="password" autoComplete="new-password" minLength={8} value={password} onChange={setPassword} />
        <Message>{error}</Message>
        <button className="btn btn-primary" disabled={busy}>Salvar</button>
      </form>
    </AuthFrame>
  );
}
