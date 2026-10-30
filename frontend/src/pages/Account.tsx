import { Link } from 'react-router-dom';
import { useAuth } from '../auth';
import { VerifyBanner } from '../components/ui';

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
        <p className="muted">Seu e-mail não aparece para os outros jogadores. Os dados são apagados no fim da campanha.</p>
      </section>
      {user.is_admin ? <Link className="btn" to="/admin">Painel admin</Link> : null}
      <button className="btn" onClick={signOut}>Sair</button>
    </div>
  );
}
