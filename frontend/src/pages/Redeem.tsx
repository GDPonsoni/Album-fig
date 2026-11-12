import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, post, type GrantView } from '../api';
import { clearNext, useAuth } from '../auth';
import { Message, VerifyBanner } from '../components/ui';
import { buzz } from '../fx';

interface Preview {
  code: string;
  pack: { name: string; slug: string; stickers_per_pack: number };
}

/** Destino do QR impresso. Abrir o link só mostra o código: o resgate é sempre o toque no botão (POST). */
export function RedeemLinkPage() {
  const { codigo = '' } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  // Vindo da câmera (`auto`): ler o QR já é a escolha de resgatar, sem o toque extra.
  const auto = useRef((useLocation().state as { auto?: boolean } | null)?.auto === true);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [grant, setGrant] = useState<GrantView | null>(null);
  const started = useRef(false);

  useEffect(() => {
    clearNext();
    api<Preview>(`/codes/preview?code=${encodeURIComponent(codigo)}`)
      .then(setPreview)
      .catch((err) => setError(err.message));
  }, [codigo]);

  useEffect(() => {
    if (auto.current && preview && user?.verified && !started.current) {
      started.current = true;
      redeem();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview, user?.verified]);

  const redeem = async () => {
    setBusy(true);
    setError('');
    try {
      setGrant(await post<GrantView>('/codes/redeem', { code: codigo }));
      buzz([30, 40, 70]);
    } catch (err) {
      setError((err as Error).message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Código</h1>
      {!user?.verified ? <VerifyBanner /> : null}
      <section className="panel paper redeem-link">
        {grant ? (
          <>
            <h2>Código resgatado!</h2>
            <p>{grant.name} com {grant.stickers_per_pack} figurinhas esperando por você.</p>
            <button className="btn btn-primary btn-big" onClick={() => navigate('/pacotes')}>Ir para os pacotes</button>
          </>
        ) : preview ? (
          <>
            <h2>Resgatar este código?</h2>
            <p className="redeem-code">{preview.code}</p>
            <p>Vale um <b>{preview.pack.name}</b> com {preview.pack.stickers_per_pack} figurinhas. Cada código só pode ser usado uma vez.</p>
            <Message>{error}</Message>
            <button className="btn btn-primary btn-big" disabled={busy || !user?.verified} onClick={redeem}>
              {busy ? 'Resgatando…' : 'Resgatar'}
            </button>
            <Link className="btn btn-ghost" to="/pacotes">Agora não</Link>
          </>
        ) : error ? (
          <>
            <Message>{error}</Message>
            <Link className="btn" to="/pacotes">Ir para os pacotes</Link>
          </>
        ) : (
          <p>Conferindo o código…</p>
        )}
      </section>
    </div>
  );
}
