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
  next_pack_at: string | null;
  server_now: string;
  refill_minutes: number;
  dev_tools: boolean;
}

function formatCountdown(ms: number) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}`;
}

// Conta pelo relógio do servidor, para não depender da hora certa no celular.
function useCountdown(data: Stock | null, onDone: () => void) {
  const [left, setLeft] = useState<number | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!data?.next_pack_at) {
      setLeft(null);
      return;
    }
    const target = Date.parse(data.next_pack_at) - (Date.parse(data.server_now) - Date.now());
    let fired = false;
    const tick = () => {
      const ms = target - Date.now();
      setLeft(ms);
      if (ms <= 0 && !fired) {
        fired = true;
        done.current();
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [data?.next_pack_at, data?.server_now]);
  return left;
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
          {onNext ? <button className="btn" onClick={onNext}>Abrir outro</button> : null}
          <button className="btn btn-ghost" onClick={onClose}>Fechar</button>
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
        <div className="opener-actions">
          <button className="btn btn-ghost">Próxima</button>
          <button className="btn btn-ghost" onClick={(event) => { event.stopPropagation(); onClose(); }}>Fechar</button>
        </div>
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

function PackTile({ grant, disabled, onOpen }: { grant: GrantView; disabled: boolean; onOpen: (grant: GrantView) => void }) {
  return (
    <button className="pack-tile" disabled={disabled} aria-label={`Abrir ${grant.name}`} onClick={() => onOpen(grant)}>
      <Wrapper grant={grant} />
      {grant.source === 'code' ? <span className="pack-tag">Código</span> : null}
    </button>
  );
}

function Drawer({ data, left, disabled, onOpen }: { data: Stock; left: number | null; disabled: boolean; onOpen: (grant: GrantView) => void }) {
  const ready = data.stock.filter((g) => g.source === 'daily').slice(0, data.daily_cap);
  const extras = data.stock.filter((g) => !ready.includes(g));
  const empty = Math.max(0, data.daily_cap - ready.length);
  const total = data.refill_minutes * 60000;
  const progress = left === null ? 0 : Math.min(100, Math.max(0, 100 - (left / total) * 100));
  const count = data.stock.length;
  return (
    <>
      <section className="ready-head">
        <h2>{count ? `${count} ${count === 1 ? 'pacote pronto' : 'pacotes prontos'}` : 'Nenhum pacote pronto'}</h2>
        <p>{count ? 'Toque em um pacote para abrir.' : 'O próximo já está a caminho.'}</p>
      </section>
      <section className="drawer" aria-label="Gaveta de pacotes">
        <ul className="drawer-slots">
          {ready.map((grant) => (
            <li key={grant.id}><PackTile grant={grant} disabled={disabled} onOpen={onOpen} /></li>
          ))}
          {Array.from({ length: empty }, (_, i) => (
            <li key={`empty-${i}`} className={`drawer-empty${i === 0 && left !== null ? ' drawer-next' : ''}`}>
              {i === 0 && left !== null ? (
                <>
                  <small>Chega em</small>
                  <strong className="drawer-time" role="timer">{formatCountdown(left)}</strong>
                  <span className="drawer-bar"><span style={{ width: `${progress}%` }} /></span>
                </>
              ) : (
                <small>Espaço livre</small>
              )}
            </li>
          ))}
        </ul>
        <p className="drawer-note">
          {empty === 0
            ? `Gaveta cheia. Próximo pacote em ${left === null ? '…' : formatCountdown(left)}, se abrir espaço até lá.`
            : empty > 1
              ? 'Cada pacote entregue já começa a contagem do próximo.'
              : 'Quando este chegar, a gaveta fica cheia.'}
        </p>
      </section>
      {extras.length ? (
        <section>
          <h2 className="extras-title">Extras</h2>
          <ul className="pack-grid">
            {extras.map((grant) => (
              <li key={grant.id}><PackTile grant={grant} disabled={disabled} onOpen={onOpen} /></li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

// Temporário: atalhos de teste, some junto com app/api/dev.py.
function DevTools({ onChanged, onCode }: { onChanged: () => void; onCode: (code: string) => void }) {
  const [state, setState] = useState<{ ok?: string; error?: string }>({});
  const run = async (action: () => Promise<string>) => {
    setState({});
    try {
      setState({ ok: await action() });
      onChanged();
    } catch (err) {
      setState({ error: (err as Error).message });
    }
  };
  const addPack = (slug: string) => run(async () => {
    await post('/dev/packs', { slug });
    return 'Pacote adicionado.';
  });
  const makeCode = (slug: string) => run(async () => {
    const { code } = await post<{ code: string }>('/dev/codes', { slug });
    onCode(code);
    return `Código ${code} preenchido no campo abaixo.`;
  });
  return (
    <section className="panel dev-tools">
      <h2>Testes (temporário)</h2>
      <div className="dev-actions">
        <button className="btn" onClick={() => addPack('comum')}>+ Pacote comum</button>
        <button className="btn" onClick={() => addPack('premium')}>+ Pacote premium</button>
        <button className="btn" onClick={() => makeCode('comum')}>Gerar código comum</button>
        <button className="btn" onClick={() => makeCode('premium')}>Gerar código premium</button>
      </div>
      {state.ok ? <Message kind="ok">{state.ok}</Message> : null}
      {state.error ? <Message>{state.error}</Message> : null}
    </section>
  );
}

function CodeReward({ grant, onOpen, onClose }: { grant: GrantView; onOpen: () => void; onClose: () => void }) {
  useEffect(() => buzz([30, 40, 70]), []);
  return (
    <div className="overlay reward" role="dialog" aria-label="Código resgatado">
      <div className="reward-confetti" aria-hidden="true">
        {Array.from({ length: 18 }, (_, i) => <i key={i} />)}
      </div>
      <p className="reward-kicker">Código resgatado!</p>
      <h2>Você ganhou um pacote</h2>
      <div className="reward-pack"><Wrapper grant={grant} /></div>
      <p className="reward-text">{grant.name} com {grant.stickers_per_pack} figurinhas esperando por você.</p>
      <div className="opener-actions">
        <button className="btn btn-primary btn-big" onClick={onOpen}>Abrir agora</button>
        <button className="btn btn-ghost" onClick={onClose}>Guardar para depois</button>
      </div>
    </div>
  );
}

function RedeemForm({ onRedeemed, disabled, code, setCode }: { onRedeemed: (grant: GrantView) => void; disabled: boolean; code: string; setCode: (code: string) => void }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const grant = await post<GrantView>('/codes/redeem', { code });
      setCode('');
      onRedeemed(grant);
    } catch (err) {
      setError((err as Error).message);
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
        <button className="btn btn-primary" disabled={busy || disabled}>{busy ? 'Resgatando…' : 'Resgatar'}</button>
      </form>
      {error ? <Message>{error}</Message> : null}
    </section>
  );
}

export function PacksPage() {
  const { user } = useAuth();
  const { setStock } = useOutletContext<{ setStock: (n: number) => void }>();
  const [data, setData] = useState<Stock | null>(null);
  const [opening, setOpening] = useState<GrantView | null>(null);
  const [error, setError] = useState('');
  const [code, setCode] = useState('');
  const [reward, setReward] = useState<GrantView | null>(null);

  const load = useCallback(async () => {
    const result = await api<Stock>('/packs');
    setData(result);
    setStock(result.stock.length);
    return result;
  }, [setStock]);

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [load]);

  const reload = useCallback(() => {
    load().catch(() => {});
  }, [load]);
  const left = useCountdown(data, reload);

  const remaining = data?.stock.filter((g) => g.id !== opening?.id) ?? [];
  const close = () => {
    setOpening(null);
    reload();
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Pacotes</h1>
      {!user?.verified ? <VerifyBanner /> : null}
      <Message>{error}</Message>
      {data ? (
        <>
          <Drawer data={data} left={left} disabled={!user?.verified} onOpen={setOpening} />
          {data.dev_tools && user?.verified ? <DevTools onChanged={reload} onCode={setCode} /> : null}
        </>
      ) : null}
      <RedeemForm onRedeemed={(grant) => { setReward(grant); reload(); }} disabled={!user?.verified} code={code} setCode={setCode} />
      {reward ? (
        <CodeReward
          grant={reward}
          onOpen={() => { setReward(null); setOpening(reward); }}
          onClose={() => setReward(null)}
        />
      ) : null}
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
