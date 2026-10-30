import { useCallback, useEffect, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { Link, useOutletContext } from 'react-router-dom';
import { api, post, type Card, type GrantView } from '../api';
import { useAuth } from '../auth';
import { Message, StickerArt, VerifyBanner } from '../components/ui';
import { buzz, lowFx } from '../fx';

type Drawn = Card & { is_new: boolean };
interface Stock {
  stock: GrantView[];
  daily_cap: number;
  daily_in_stock: number;
}

function Wrapper({ grant }: { grant: GrantView }) {
  return (
    <div className={`foil foil-${grant.slug === 'premium' ? 'gold' : 'blue'}`}>
      <span className="foil-brand">Álbum da Calourada</span>
      <strong>{grant.name.replace('Pacote ', '')}</strong>
      <small>{grant.stickers_per_pack} figurinhas</small>
    </div>
  );
}

function Opener({ grant, onClose, onNext }: { grant: GrantView; onClose: () => void; onNext: (() => void) | null }) {
  const [stage, setStage] = useState<'sealed' | 'tearing' | 'reveal' | 'summary'>('sealed');
  const [items, setItems] = useState<Drawn[]>([]);
  const [shown, setShown] = useState(0);
  const [error, setError] = useState('');
  const startX = useRef<number | null>(null);
  const started = useRef(false);

  const tear = useCallback(async () => {
    if (started.current) return;
    started.current = true;
    setStage('tearing');
    buzz(20);
    const wait = new Promise((resolve) => window.setTimeout(resolve, lowFx() ? 0 : 520));
    try {
      const [result] = await Promise.all([post<{ items: Drawn[] }>(`/packs/${grant.id}/open`), wait]);
      setItems(result.items);
      setStage('reveal');
      announce(result.items[0]);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [grant.id]);

  const onDown = (event: ReactPointerEvent) => {
    startX.current = event.clientX;
  };
  const onMove = (event: ReactPointerEvent) => {
    if (startX.current !== null && Math.abs(event.clientX - startX.current) > 50) tear();
  };

  const next = () => {
    const following = shown + 1;
    if (following >= items.length) {
      setStage('summary');
      return;
    }
    setShown(following);
    announce(items[following]);
  };

  if (error) {
    return (
      <div className="overlay opener">
        <Message>{error}</Message>
        <button className="btn" onClick={onClose}>Voltar</button>
      </div>
    );
  }

  if (stage === 'summary') {
    const fresh = items.filter((item) => item.is_new).length;
    return (
      <div className="overlay opener opener-summary">
        <h2>{fresh ? `${fresh} ${fresh === 1 ? 'nova' : 'novas'}!` : 'Só repetidas desta vez'}</h2>
        <p>{items.length - fresh ? `${items.length - fresh} repetida(s) para trocar` : 'Nenhuma repetida'}</p>
        <div className="summary-grid">
          {items.map((item, i) => (
            <div key={i} className="summary-item">
              <StickerArt card={item} />
              <em className={item.is_new ? 'seal-new' : 'seal-dup'}>{item.is_new ? 'Nova' : 'Repetida'}</em>
            </div>
          ))}
        </div>
        <div className="opener-actions">
          <Link className="btn btn-primary" to="/">Colar no álbum</Link>
          {onNext ? <button className="btn" onClick={onNext}>Abrir outro</button> : <button className="btn" onClick={onClose}>Fechar</button>}
        </div>
      </div>
    );
  }

  if (stage === 'reveal') {
    const item = items[shown];
    return (
      <div className="overlay opener" onClick={next}>
        <p className="opener-count">{shown + 1} de {items.length}</p>
        <div key={shown} className="reveal">
          <StickerArt card={item} shine={item.rarity.rank >= 2} />
          <em className={item.is_new ? 'seal-new' : 'seal-dup'}>{item.is_new ? 'Nova' : 'Repetida'}</em>
        </div>
        <p className="reveal-name">{item.name}</p>
        <p className="reveal-meta" style={{ color: item.rarity.rank >= 2 ? item.rarity.color : undefined }}>
          {item.rarity.name} · {item.org_name}
        </p>
        <button className="btn btn-ghost">Próxima</button>
      </div>
    );
  }

  return (
    <div className="overlay opener">
      <div
        className={`tear-area${stage === 'tearing' ? ' torn' : ''}`}
        role="button"
        tabIndex={0}
        aria-label="Rasgar pacote"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onClick={tear}
        onKeyDown={(event) => event.key === 'Enter' && tear()}
      >
        <div className="foil-strip"><Wrapper grant={grant} /></div>
        <div className="foil-body"><Wrapper grant={grant} /></div>
      </div>
      <p className="opener-hint">{stage === 'sealed' ? 'Deslize ou toque para rasgar' : 'Abrindo…'}</p>
      {stage === 'sealed' ? <button className="btn btn-ghost" onClick={onClose}>Agora não</button> : null}
    </div>
  );
}

function announce(item?: Drawn) {
  if (item && item.rarity.rank >= 2) buzz(item.rarity.rank >= 3 ? [40, 40, 80] : 50);
}

function RedeemForm({ onRedeemed, disabled }: { onRedeemed: () => void; disabled: boolean }) {
  const [code, setCode] = useState('');
  const [state, setState] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setState({});
    try {
      const grant = await post<GrantView>('/codes/redeem', { code });
      setState({ ok: `${grant.name} adicionado ao seu estoque.` });
      setCode('');
      onRedeemed();
    } catch (err) {
      setState({ error: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel paper">
      <h2>Achou um código?</h2>
      <p className="muted">Eles estão espalhados pelo campus. Cada código vale um pacote extra e só pode ser usado uma vez.</p>
      <form className="code-form" onSubmit={submit}>
        <input
          aria-label="Código"
          placeholder="XXXX-XXXX-XXXX-XXXX"
          autoCapitalize="characters"
          autoComplete="off"
          spellCheck={false}
          required
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <button className="btn btn-primary" disabled={busy || disabled}>Resgatar</button>
      </form>
      {state.ok ? <Message kind="ok">{state.ok}</Message> : null}
      {state.error ? <Message>{state.error}</Message> : null}
    </section>
  );
}

export function PacksPage() {
  const { user } = useAuth();
  const { setStock } = useOutletContext<{ setStock: (n: number) => void }>();
  const [data, setData] = useState<Stock | null>(null);
  const [opening, setOpening] = useState<GrantView | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const result = await api<Stock>('/packs');
    setData(result);
    setStock(result.stock.length);
    return result;
  }, [setStock]);

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [load]);

  const remaining = data?.stock.filter((g) => g.id !== opening?.id) ?? [];
  const close = () => {
    setOpening(null);
    load().catch(() => {});
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Pacotes</h1>
      {!user?.verified ? <VerifyBanner /> : null}
      <Message>{error}</Message>
      {data ? (
        <>
          {data.stock.length ? (
            <ul className="pack-grid">
              {data.stock.map((grant) => (
                <li key={grant.id}>
                  <button
                    className="pack-tile"
                    disabled={!user?.verified}
                    aria-label={`Abrir ${grant.name}`}
                    onClick={() => setOpening(grant)}
                  >
                    <Wrapper grant={grant} />
                    {grant.source === 'code' ? <span className="pack-tag">Código</span> : null}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <section className="panel paper empty">
              <h2>Sem pacotes agora</h2>
              <p className="muted">Novos pacotes chegam depois da meia-noite. Enquanto isso, procure códigos pelo campus ou troque repetidas.</p>
            </section>
          )}
          <p className="muted stock-note">Estoque diário: {data.daily_in_stock} de {data.daily_cap}. Acima do limite, o pacote do dia se perde.</p>
        </>
      ) : null}
      <RedeemForm onRedeemed={() => load().catch(() => {})} disabled={!user?.verified} />
      {opening ? (
        <Opener
          key={opening.id}
          grant={opening}
          onClose={close}
          onNext={remaining.length ? () => { setData((d) => (d ? { ...d, stock: remaining } : d)); setOpening(remaining[0]); } : null}
        />
      ) : null}
    </div>
  );
}
