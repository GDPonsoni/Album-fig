import QRCode from 'qrcode';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { post } from '../api';
import { useAuth } from '../auth';
import { Message, VerifyBanner } from '../components/ui';
import { MyAlbumsSection } from './MyAlbums';

/** Verificação em duas etapas do superadmin: sem ela não dá para aprovar, publicar nem conceder papéis. */
function TwoFactor() {
  const { user, refresh } = useAuth();
  const [password, setPassword] = useState('');
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (setup && canvas.current) QRCode.toCanvas(canvas.current, setup.otpauth_uri, { width: 220, margin: 1 });
  }, [setup]);

  const guard = async (action: () => Promise<void>) => {
    setError('');
    setNote('');
    try {
      await action();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const start = (event: FormEvent) => {
    event.preventDefault();
    guard(async () => {
      setSetup(await post('/auth/2fa/setup', { password }));
      setPassword('');
    });
  };
  const enable = (event: FormEvent) => {
    event.preventDefault();
    guard(async () => {
      const result = await post<{ recovery_codes: string[] }>('/auth/2fa/enable', { code });
      setCodes(result.recovery_codes);
      setSetup(null);
      setCode('');
      await refresh();
    });
  };
  const verify = (event: FormEvent) => {
    event.preventDefault();
    guard(async () => {
      await post('/auth/2fa/verify', { code });
      setCode('');
      setNote('Código confirmado nesta sessão.');
      await refresh();
    });
  };
  const disable = (event: FormEvent) => {
    event.preventDefault();
    guard(async () => {
      await post('/auth/2fa/disable', { password });
      setPassword('');
      setCodes(null);
      setNote('Verificação em duas etapas desligada.');
      await refresh();
    });
  };

  if (!user) return null;
  return (
    <section className="panel paper two-factor" aria-label="Verificação em duas etapas">
      <h2>Verificação em duas etapas</h2>
      <Message>{error}</Message>
      {note ? <Message kind="ok">{note}</Message> : null}
      {codes ? (
        <div className="recovery">
          <p><b>Guarde estes códigos de recuperação.</b> Cada um vale uma vez, se você ficar sem o celular. Eles não serão mostrados de novo.</p>
          <ul>{codes.map((item) => <li key={item}><code>{item}</code></li>)}</ul>
          <button className="btn" onClick={() => setCodes(null)}>Já guardei</button>
        </div>
      ) : null}
      {user.mfa_enabled ? (
        <>
          <p className="muted">
            Ativa. {user.mfa_verified ? 'Código confirmado nesta sessão.' : 'Nesta sessão o código ainda não foi confirmado.'}
          </p>
          {!user.mfa_verified ? (
            <form className="stack" onSubmit={verify}>
              <label className="field"><span>Código do app (ou de recuperação)</span>
                <input required minLength={6} maxLength={20} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
              </label>
              <button className="btn btn-primary">Confirmar código</button>
            </form>
          ) : null}
          <form className="stack" onSubmit={disable}>
            <label className="field"><span>Senha, para desligar</span>
              <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button className="btn">Desligar</button>
          </form>
        </>
      ) : setup ? (
        <form className="stack" onSubmit={enable}>
          <p>Leia o QR no app autenticador (Google Authenticator, Authy, 1Password…) e digite o código de 6 dígitos.</p>
          <canvas ref={canvas} aria-label="QR do segredo de duas etapas" />
          <p className="muted">Sem câmera? Digite a chave: <code className="secret">{setup.secret}</code></p>
          <label className="field"><span>Código de 6 dígitos</span>
            <input required inputMode="numeric" minLength={6} maxLength={7} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <button className="btn btn-primary">Ativar</button>
        </form>
      ) : (
        <form className="stack" onSubmit={start}>
          <p className="muted">Obrigatória para aprovar e publicar revisões e, no superadmin, para conceder papéis.</p>
          <label className="field"><span>Sua senha</span>
            <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <button className="btn btn-primary">Começar a ativação</button>
        </form>
      )}
    </section>
  );
}

export function AccountPage() {
  const { user, signOut } = useAuth();
  if (!user) return null;
  return (
    <div className="screen">
      <h1 className="screen-title">Conta</h1>
      {!user.verified ? <VerifyBanner /> : null}
      <section className="panel paper">
        <dl className="facts">
          <dt>Apelido</dt>
          <dd>{user.nickname}</dd>
          <dt>E-mail</dt>
          <dd>{user.email}</dd>
        </dl>
        <p className="muted">Seu e-mail não aparece para os outros jogadores. Os álbuns que você jogou ficam guardados na sua conta.</p>
      </section>
      <MyAlbumsSection />
      <button className="btn" onClick={signOut}>Sair</button>
      {user.is_admin || user.editor_chapters || user.reviewer_chapters || user.promoter ? (
        <section className="role-zone" aria-label="Áreas de trabalho">
          <h2>Áreas de trabalho</h2>
          <p>Você tem cargos neste álbum. Cada um abre uma área própria.</p>
          {user.is_admin ? (
            <div className="role-card role-admin">
              <span className="role-badge">Superadmin</span>
              <p>Gerencia álbuns, códigos, cargos e o rastreio de tudo.</p>
              <div className="role-links">
                <Link className="btn" to="/admin">Painel admin</Link>
                <Link className="btn" to="/admin/revisoes">Revisões e cargos</Link>
                <Link className="btn" to="/admin/rastreio">Rastreio</Link>
              </div>
            </div>
          ) : null}
          {user.is_admin || user.reviewer_chapters ? (
            <div className="role-card role-reviewer">
              <span className="role-badge">Revisor</span>
              <p>Aprova ou rejeita o que os editores enviam e publica no álbum.</p>
              <div className="role-links"><Link className="btn" to="/admin/revisoes">Revisar capítulos</Link></div>
            </div>
          ) : null}
          {user.editor_chapters ? (
            <div className="role-card role-editor">
              <span className="role-badge">Editor</span>
              <p>Personaliza o seu capítulo e envia para revisão.</p>
              <div className="role-links"><Link className="btn" to="/editor">Editar meu capítulo</Link></div>
            </div>
          ) : null}
          {user.promoter ? (
            <div className="role-card role-promoter">
              <span className="role-badge">Promotor</span>
              <p>Gera códigos de pacote extra, com QR para distribuir.</p>
              <div className="role-links"><Link className="btn" to="/promotor">Gerar códigos de pacote</Link></div>
            </div>
          ) : null}
          {user.is_admin || user.reviewer_chapters ? <TwoFactor /> : null}
        </section>
      ) : null}
    </div>
  );
}
