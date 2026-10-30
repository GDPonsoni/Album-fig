import QRCode from 'qrcode';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, api, post, type Card, type TradeView } from '../api';
import { useAuth } from '../auth';
import { Message, StickerArt, VerifyBanner } from '../components/ui';
import { buzz } from '../fx';

type Duplicate = Card & { available: number };

function DuplicatesPanel({ dupes }: { dupes: Duplicate[] }) {
  const total = dupes.reduce((sum, item) => sum + item.available, 0);
  return (
    <section className="panel paper dupes-list">
      <h2>Minhas repetidas{total ? ` (${total})` : ''}</h2>
      {dupes.length ? (
        <ul>
          {dupes.map((item) => (
            <li key={item.id}>
              <div className="dupes-art">
                <StickerArt card={item} />
                <b className="qty">×{item.available}</b>
              </div>
              <span>{item.name}</span>
              <small>{item.org_name}</small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Nenhuma repetida ainda. As que sobrarem dos pacotes aparecem aqui.</p>
      )}
    </section>
  );
}

export function TradeHome() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dupes, setDupes] = useState<Duplicate[] | null>(null);

  useEffect(() => {
    api<{ token: string | null }>('/trades/current')
      .then((result) => result.token && navigate(`/trocar/${result.token}`, { replace: true }))
      .catch(() => {});
    api<Duplicate[]>('/inventory/duplicates')
      .then(setDupes)
      .catch(() => {});
  }, [navigate]);

  const start = async () => {
    setBusy(true);
    setError('');
    try {
      const trade = await post<TradeView>('/trades');
      navigate(`/trocar/${trade.token}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Trocar</h1>
      {!user?.verified ? <VerifyBanner /> : null}
      <section className="panel paper trade-intro">
        <h2>Encontre alguém e troque ao vivo</h2>
        <ol>
          <li>Toque em <b>Trocar</b> para gerar um QR.</li>
          <li>A outra pessoa aponta a câmera do celular para ele.</li>
          <li>Cada um escolhe as repetidas e os dois aceitam.</li>
        </ol>
        <Message>{error}</Message>
        <button className="btn btn-primary btn-big" disabled={busy || !user?.verified} onClick={start}>Trocar</button>
      </section>
      {dupes ? <DuplicatesPanel dupes={dupes} /> : null}
    </div>
  );
}

function Countdown({ seconds, onZero }: { seconds: number; onZero: () => void }) {
  const [deadline, setDeadline] = useState(() => Date.now() + seconds * 1000);
  const [left, setLeft] = useState(seconds);
  useEffect(() => setDeadline(Date.now() + seconds * 1000), [seconds]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      const value = Math.max(0, (deadline - Date.now()) / 1000);
      setLeft(value);
      if (value <= 0) {
        window.clearInterval(timer);
        onZero();
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [deadline, onZero]);
  const whole = Math.ceil(left);
  return (
    <span className={`countdown${whole <= 30 ? ' urgent' : ''}`} role="timer">
      {Math.floor(whole / 60)}:{String(whole % 60).padStart(2, '0')}
    </span>
  );
}

function Waiting({ trade, refresh }: { trade: TradeView; refresh: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [copied, setCopied] = useState(false);
  const link = `${window.location.origin}/trocar/${trade.token}`;
  useEffect(() => {
    if (canvas.current) QRCode.toCanvas(canvas.current, link, { width: 260, margin: 1, color: { dark: '#1f2a44', light: '#fffdf6' } });
  }, [link]);
  const share = async () => {
    if (navigator.share) {
      await navigator.share({ title: 'Troca de figurinhas', url: link }).catch(() => {});
      return;
    }
    await navigator.clipboard?.writeText(link);
    setCopied(true);
  };
  return (
    <section className="panel paper waiting">
      <h2>Mostre o QR para a outra pessoa</h2>
      <canvas ref={canvas} aria-label="QR code da troca" />
      <p className="muted">O link vale para uma pessoa e expira em <Countdown seconds={trade.expires_in} onZero={refresh} /></p>
      <button className="btn" onClick={share}>{copied ? 'Link copiado' : 'Compartilhar link'}</button>
    </section>
  );
}

function OfferColumn({ title, items, accepted, onRemove }: { title: string; items: TradeView['my_offer']; accepted: boolean; onRemove?: (id: number) => void }) {
  return (
    <section className={`offer-col${accepted ? ' is-accepted' : ''}`}>
      <h3>{title}{accepted ? <span className="accepted-mark">Aceitou</span> : null}</h3>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item.id}>
              <button disabled={!onRemove} aria-label={onRemove ? `Tirar ${item.name} da oferta` : item.name} onClick={() => onRemove?.(item.id)}>
                <StickerArt card={item} />
                {item.quantity > 1 ? <b className="qty">×{item.quantity}</b> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="offer-empty">Nada ainda</p>
      )}
    </section>
  );
}

function ActiveTrade({ trade, setTrade, refresh }: { trade: TradeView; setTrade: (t: TradeView) => void; refresh: () => void }) {
  const [dupes, setDupes] = useState<Duplicate[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Duplicate[]>('/inventory/duplicates').then(setDupes).catch((err) => setError(err.message));
  }, []);

  const run = async (action: () => Promise<TradeView>) => {
    setBusy(true);
    setError('');
    try {
      setTrade(await action());
    } catch (err) {
      setError((err as Error).message);
      refresh();
    } finally {
      setBusy(false);
    }
  };

  const sendOffer = (quantities: Map<number, number>) =>
    run(() =>
      api<TradeView>(`/trades/${trade.token}/offer`, {
        method: 'PUT',
        body: { items: [...quantities].filter(([, q]) => q > 0).map(([sticker_id, quantity]) => ({ sticker_id, quantity })) },
      }),
    );
  const current = () => new Map(trade.my_offer.map((item) => [item.id, item.quantity]));
  const add = (item: Duplicate) => {
    const map = current();
    map.set(item.id, Math.min((map.get(item.id) ?? 0) + 1, item.available));
    sendOffer(map);
  };
  const remove = (id: number) => {
    const map = current();
    map.set(id, (map.get(id) ?? 0) - 1);
    sendOffer(map);
  };

  const canAccept = trade.my_offer.length > 0 && trade.other_offer.length > 0 && !trade.i_accepted;
  const offered = current();

  return (
    <>
      <div className="trade-head">
        <div>
          <small>Trocando com</small>
          <strong>{trade.other_nickname}</strong>
        </div>
        <Countdown seconds={trade.expires_in} onZero={refresh} />
      </div>
      <div className="offers">
        <OfferColumn title="Minha oferta" items={trade.my_offer} accepted={trade.i_accepted} onRemove={busy ? undefined : remove} />
        <OfferColumn title={`Oferta de ${trade.other_nickname}`} items={trade.other_offer} accepted={trade.other_accepted} />
      </div>
      <Message>{error}</Message>
      <button
        className="btn btn-primary btn-big"
        disabled={!canAccept || busy}
        onClick={() => run(() => post<TradeView>(`/trades/${trade.token}/accept`, { version: trade.offer_version }))}
      >
        {trade.i_accepted ? `Esperando ${trade.other_nickname}…` : 'Aceitar troca'}
      </button>
      <section className="panel paper dupes">
        <h3>Minhas repetidas</h3>
        {dupes.length ? (
          <ul>
            {dupes.map((item) => {
              const left = item.available - (offered.get(item.id) ?? 0);
              return (
                <li key={item.id}>
                  <button disabled={left <= 0 || busy} aria-label={`Oferecer ${item.name}`} onClick={() => add(item)}>
                    <StickerArt card={item} />
                    <b className="qty">{left}</b>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">Você não tem repetidas para oferecer agora.</p>
        )}
      </section>
    </>
  );
}

const ENDINGS: Record<string, { title: string; text: string }> = {
  done: { title: 'Troca feita!', text: 'As figurinhas novas estão na sua bandeja, prontas para colar.' },
  cancelled: { title: 'Troca cancelada', text: 'Nenhuma figurinha mudou de mão.' },
  expired: { title: 'O tempo acabou', text: 'Nenhuma figurinha mudou de mão. Dá para começar outra troca.' },
};

export function TradeSession() {
  const { token = '' } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [trade, setTrade] = useState<TradeView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const previous = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setTrade(await api<TradeView>(`/trades/${token}`));
    } catch (err) {
      if (err instanceof ApiError && err.status !== 0) setError(err.message);
    }
  }, [token]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const state = trade?.state;
  const live = state === 'open' || state === 'active';

  // WebSocket avisa que algo mudou; a leitura periódica cobre conexões que caem.
  useEffect(() => {
    if (!live) return;
    let closed = false;
    let socket: WebSocket | null = null;
    let retry = 0;
    const connect = () => {
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(`${scheme}://${window.location.host}/api/trades/${token}/ws`);
      socket.onmessage = () => refresh();
      socket.onclose = () => {
        if (!closed) retry = window.setTimeout(connect, 2000);
      };
    };
    connect();
    const poll = window.setInterval(refresh, 5000);
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(poll);
      socket?.close();
    };
  }, [live, token, refresh]);

  useEffect(() => {
    if (state && state !== previous.current && (state === 'active' || state === 'done')) buzz(state === 'done' ? [40, 40, 80] : 30);
    previous.current = state ?? null;
  }, [state]);

  const cancel = async () => {
    setBusy(true);
    await post<TradeView>(`/trades/${token}/cancel`).then(setTrade).catch((err) => setError(err.message));
    setBusy(false);
  };
  const join = async () => {
    setBusy(true);
    setError('');
    try {
      setTrade(await post<TradeView>(`/trades/${token}/join`));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error && !trade) {
    return (
      <div className="screen">
        <h1 className="screen-title">Trocar</h1>
        <section className="panel paper empty">
          <Message>{error}</Message>
          <Link className="btn" to="/trocar">Começar outra troca</Link>
        </section>
      </div>
    );
  }
  if (!trade) return <div className="screen"><p className="muted">Carregando troca…</p></div>;

  if (trade.state === 'invite') {
    return (
      <div className="screen">
        <h1 className="screen-title">Trocar</h1>
        {!user?.verified ? <VerifyBanner /> : null}
        <section className="panel paper empty">
          <h2>{trade.other_nickname} chamou você para trocar</h2>
          <Message>{error}</Message>
          <button className="btn btn-primary btn-big" disabled={busy || !user?.verified} onClick={join}>Entrar na troca</button>
        </section>
      </div>
    );
  }

  const ending = ENDINGS[trade.state];
  if (ending) {
    return (
      <div className="screen">
        <h1 className="screen-title">Trocar</h1>
        <section className={`panel paper empty ending-${trade.state}`}>
          <h2>{ending.title}</h2>
          <p>{ending.text}</p>
          {trade.state === 'done' ? <Link className="btn btn-primary" to="/">Ir para o álbum</Link> : null}
          <button className="btn" onClick={() => navigate('/trocar')}>Nova troca</button>
        </section>
      </div>
    );
  }

  return (
    <div className="screen trade-screen">
      {trade.state === 'open' ? <Waiting trade={trade} refresh={refresh} /> : <ActiveTrade trade={trade} setTrade={setTrade} refresh={refresh} />}
      <button className="btn btn-danger" disabled={busy} onClick={cancel}>Cancelar troca</button>
    </div>
  );
}
