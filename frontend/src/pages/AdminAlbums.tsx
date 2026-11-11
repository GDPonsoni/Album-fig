import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, post } from '../api';
import { Message } from '../components/ui';

type Row = Record<string, any>;

const STATUS: Record<string, string> = { draft: 'Rascunho', scheduled: 'Agendado', active: 'Em andamento', ended: 'Encerrado' };
const PARAM_LABEL: Record<string, string> = {
  'daily.refill_minutes': 'Minutos entre pacotes da gaveta',
  'daily.stock_cap': 'Limite da gaveta (pacotes)',
  'daily.welcome_packs': 'Pacotes de boas-vindas',
  'daily.premium_every_n_days': 'Premium a cada N dias (0 desliga)',
  'daily.common_pack_slug': 'Pacote comum (identificador)',
  'daily.premium_pack_slug': 'Pacote premium (identificador)',
  'daily.timezone': 'Fuso horário',
  'trade.link_ttl_seconds': 'Validade do link de troca (s)',
  'trade.session_ttl_seconds': 'Duração da troca (s)',
};
// Os parâmetros que entram na estimativa e mudam o resultado na hora.
const ESTIMATE_KEYS = ['daily.refill_minutes', 'daily.stock_cap', 'daily.welcome_packs', 'daily.premium_every_n_days'];

const toLocal = (iso: string) => {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const fromLocal = (value: string) => new Date(value).toISOString();
const when = (iso: string) => new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

async function upload(file: File, name: string): Promise<string> {
  const form = new FormData();
  form.append('file', file);
  form.append('name', name);
  return (await api<{ url: string }>('/admin/upload', { form })).url;
}

function ImageInput({ value, name, onChange }: { value: string | null; name: string; onChange: (url: string | null) => void }) {
  const [note, setNote] = useState('');
  return (
    <div className="image-field">
      {value ? <img src={value} alt="" /> : null}
      <input
        type="file"
        accept="image/png,image/webp,image/jpeg"
        onChange={async (event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          setNote('Enviando…');
          try {
            onChange(await upload(file, name));
            setNote('');
          } catch (error) {
            setNote((error as Error).message);
          }
        }}
      />
      {value ? <button type="button" className="link" onClick={() => onChange(null)}>Remover</button> : null}
      {note ? <small>{note}</small> : null}
    </div>
  );
}

function AlbumForm({ initial, onSaved, onCancel }: { initial: Row | null; onSaved: () => void; onCancel: () => void }) {
  const start = new Date(Date.now() + 86400000).toISOString();
  const end = new Date(Date.now() + 91 * 86400000).toISOString();
  const [form, setForm] = useState<Row>({
    title: initial?.title ?? '',
    cover_url: initial?.cover_url ?? null,
    starts_at: toLocal(initial?.starts_at ?? start),
    ends_at: toLocal(initial?.ends_at ?? end),
    ready: initial?.state === 'ready',
  });
  const [error, setError] = useState('');
  const active = initial?.status === 'active';
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    const body = {
      title: form.title,
      cover_url: form.cover_url,
      // Em andamento o início não muda: reenviar o valor exato evita diferença de arredondamento.
      starts_at: active ? initial!.starts_at : fromLocal(form.starts_at),
      ends_at: fromLocal(form.ends_at),
      state: form.ready ? 'ready' : 'draft',
    };
    try {
      if (initial) await api(`/admin/albums/${initial.id}`, { method: 'PUT', body });
      else await post('/admin/albums', body);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <form className="admin-form" onSubmit={save}>
      <label className="field"><span>Título</span><input required maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
      <label className="field"><span>Capa</span><ImageInput value={form.cover_url} name={`albuns/${form.title || 'sem-titulo'}/capa`} onChange={(url) => setForm({ ...form, cover_url: url })} /></label>
      <label className="field"><span>Início</span><input type="datetime-local" required disabled={active} value={form.starts_at} onChange={(e) => setForm({ ...form, starts_at: e.target.value })} /></label>
      <label className="field"><span>Fim</span><input type="datetime-local" required value={form.ends_at} onChange={(e) => setForm({ ...form, ends_at: e.target.value })} /></label>
      <label className="field">
        <span>Pronto para ir ao ar</span>
        <input type="checkbox" disabled={active} checked={form.ready} onChange={(e) => setForm({ ...form, ready: e.target.checked })} />
        <small>Pronto, o álbum entra no ar sozinho na data de início. Só um álbum pronto por período.</small>
      </label>
      <Message>{error}</Message>
      <div className="admin-actions">
        <button className="btn btn-primary">Salvar</button>
        <button type="button" className="btn" onClick={onCancel}>Cancelar</button>
      </div>
    </form>
  );
}

function Chapters({ album }: { album: Row }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [orgs, setOrgs] = useState<Row[]>([]);
  const [rarities, setRarities] = useState<Row[]>([]);
  const [error, setError] = useState('');
  const [ok, setOk] = useState('');
  const [pick, setPick] = useState('');
  const [fresh, setFresh] = useState({ name: '', theme_color: '#1d4ed8' });
  const base = `/admin/albums/${album.id}/chapters`;
  const frozen = album.status === 'active' || album.status === 'ended';
  const load = useCallback(() => api<Row[]>(base).then(setRows).catch((e) => setError(e.message)), [base]);
  useEffect(() => {
    load();
    api<Row[]>('/admin/orgs').then(setOrgs).catch(() => {});
    api<Row[]>('/admin/rarities').then(setRarities).catch(() => {});
  }, [load]);

  const run = async (action: () => Promise<string | void>) => {
    setError('');
    setOk('');
    try {
      setOk((await action()) || '');
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const used = new Set(rows.map((row) => row.org_id));
  const add = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      await post(base, pick ? { org_id: Number(pick), sort_order: rows.length } : { org: fresh, sort_order: rows.length });
      setPick('');
      setFresh({ name: '', theme_color: '#1d4ed8' });
    });
  };
  const update = (row: Row, changes: Row) =>
    run(() => api(`${base}/${row.id}`, { method: 'PUT', body: { sort_order: row.sort_order, mosaic_image_url: row.mosaic_image_url, ...changes } }));
  const mosaic = (row: Row) => {
    const shape = window.prompt('Colunas x linhas do mosaico. As peças atuais serão substituídas.', `${row.mosaic_cols}x${row.mosaic_rows}`);
    const match = shape?.match(/^(\d+)\s*x\s*(\d+)$/);
    if (!match) return;
    run(async () => {
      const result = await post(`${base}/${row.id}/mosaic`, { cols: Number(match[1]), rows: Number(match[2]), rarity_id: rarities[0]?.id });
      return `${result.pieces} peças geradas.`;
    });
  };

  return (
    <section>
      <h3>Capítulos ({rows.length})</h3>
      {frozen ? <p className="muted">Álbum {album.status === 'ended' ? 'encerrado: nada muda' : 'em andamento: a estrutura está congelada; só ordem e imagens mudam'}.</p> : null}
      <Message>{error}</Message>
      {ok ? <Message kind="ok">{ok}</Message> : null}
      <ul className="admin-list">
        {rows.map((row) => (
          <li key={row.id}>
            <span><i className="dot" style={{ background: row.org_color }} /> {row.org_name} · {row.stickers} figurinhas</span>
            <span className="admin-row-actions">
              <label className="inline-field">Ordem
                <input type="number" defaultValue={row.sort_order} disabled={album.status === 'ended'} onBlur={(e) => Number(e.target.value) !== row.sort_order && update(row, { sort_order: Number(e.target.value) })} />
              </label>
              {album.status !== 'ended' ? (
                <ImageInput value={row.mosaic_image_url} name={`orgs/${row.org_name}/mosaico`} onChange={(url) => update(row, { mosaic_image_url: url })} />
              ) : null}
              {!frozen ? <button className="link" onClick={() => mosaic(row)}>Gerar mosaico</button> : null}
              {!frozen ? <button className="link danger" onClick={() => window.confirm(`Tirar ${row.org_name} deste álbum?`) && run(() => api(`${base}/${row.id}`, { method: 'DELETE' }))}>Remover</button> : null}
            </span>
          </li>
        ))}
      </ul>
      {!frozen ? (
        <form className="admin-form" onSubmit={add}>
          <label className="field">
            <span>Adicionar org existente</span>
            <select value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Criar uma org nova…</option>
              {orgs.filter((org) => !used.has(org.id)).map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
            </select>
          </label>
          {!pick ? (
            <>
              <label className="field"><span>Nome da org nova</span><input required value={fresh.name} onChange={(e) => setFresh({ ...fresh, name: e.target.value })} /></label>
              <label className="field"><span>Cor tema</span><input type="color" value={fresh.theme_color} onChange={(e) => setFresh({ ...fresh, theme_color: e.target.value })} /></label>
            </>
          ) : null}
          <div className="admin-actions"><button className="btn btn-primary">Adicionar capítulo</button></div>
        </form>
      ) : null}
    </section>
  );
}

function Estimate({ result }: { result: Row }) {
  const days = result.days_to as Record<string, { day: number | null; within_album: boolean }>;
  const milestones: number[] = result.scenarios[0]?.milestones.map((m: Row) => m.day) ?? [];
  return (
    <div className="estimate">
      {result.alerts.map((alert: Row) => (
        <p key={alert.code} className={`estimate-alert estimate-${alert.level}`} role="alert">{alert.message}</p>
      ))}
      {result.alerts.length ? null : <p className="estimate-alert estimate-ok">Dentro da faixa: nem generoso nem apertado demais.</p>}
      <table className="estimate-table">
        <caption>Álbum esperado (sem trocas) por dia de entrada</caption>
        <thead>
          <tr><th>Entra no dia</th>{milestones.map((day) => <th key={day}>Dia {day}</th>)}</tr>
        </thead>
        <tbody>
          {result.scenarios.map((scenario: Row) => (
            <tr key={scenario.entry_day}>
              <th>{scenario.entry_day}</th>
              {milestones.map((day) => {
                const point = scenario.milestones.find((m: Row) => m.day === day);
                return <td key={day}>{point ? `${point.percent}%` : '—'}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="estimate-facts">
        {(['80', '95', '100'] as const).map((label) => (
          <div key={label}>
            <dt>{label}% no dia</dt>
            <dd>{days[label].day === null ? 'nunca' : `${days[label].day}${days[label].within_album ? '' : ' (fora do álbum)'}`}</dd>
          </div>
        ))}
        <div><dt>Pacotes no álbum</dt><dd>{result.totals.packs}</dd></div>
        <div><dt>Figurinhas recebidas</dt><dd>{result.totals.stickers_received}</dd></div>
        <div><dt>Repetidas esperadas</dt><dd>{result.totals.duplicates}</dd></div>
        <div><dt>Pacotes por dia</dt><dd>{result.packs_per_day}</dd></div>
        <div><dt>Figurinhas no álbum</dt><dd>{result.total_stickers}</dd></div>
      </dl>
      <p className="muted">
        {result.stickers_by_rarity.map((r: Row) => `${r.count} ${r.rarity}`).join(' · ')}. {result.notes.join(' ')}
      </p>
    </div>
  );
}

function Params({ album }: { album: Row }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [scenario, setScenario] = useState({ open_fraction: 100, visits_per_day: 1 });
  const [result, setResult] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  const base = `/admin/albums/${album.id}/config`;
  const closed = album.status === 'ended';

  const load = useCallback(async () => {
    const data = await api<Row[]>(base);
    setRows(data);
    setDraft(Object.fromEntries(data.map((row) => [row.key, String(row.effective)])));
  }, [base]);
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  // A estimativa acompanha o que está digitado, antes de salvar.
  const request = useRef(0);
  useEffect(() => {
    if (!rows.length) return;
    const body: Row = { album_id: album.id, open_fraction: scenario.open_fraction / 100, visits_per_day: scenario.visits_per_day };
    for (const key of ESTIMATE_KEYS) {
      const value = Number(draft[key]);
      if (draft[key] === '' || !Number.isFinite(value)) return;
      body[key.replace('daily.', '')] = value;
    }
    const run = ++request.current;
    const timer = window.setTimeout(() => {
      post<Row>('/admin/albums/estimate', body)
        .then((data) => run === request.current && (setResult(data), setError('')))
        .catch((e) => run === request.current && setError(e.message));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [album.id, draft, rows.length, scenario]);

  const changed = rows.filter((row) => draft[row.key] !== String(row.effective));
  const save = async () => {
    setError('');
    setSaved('');
    try {
      for (const row of changed) {
        const value = typeof row.default === 'number' ? Number(draft[row.key]) : draft[row.key];
        // Igual ao padrão global: remove a sobrescrita em vez de gravar uma cópia.
        await api(`${base}/${row.key}`, { method: 'PUT', body: { value: value === row.default ? null : value } });
      }
      await load();
      setSaved('Parâmetros do álbum salvos.');
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section>
      <h3>Parâmetros e estimativa</h3>
      <p className="muted">Valem só para este álbum; o que não for alterado segue o padrão global.</p>
      <Message>{error}</Message>
      {saved ? <Message kind="ok">{saved}</Message> : null}
      <div className="config-list">
        {rows.map((row) => (
          <label key={row.key} className="field">
            <span>{PARAM_LABEL[row.key] ?? row.key}</span>
            <input
              type={typeof row.default === 'number' ? 'number' : 'text'}
              min={typeof row.default === 'number' ? 0 : undefined}
              disabled={closed}
              value={draft[row.key] ?? ''}
              onChange={(e) => setDraft({ ...draft, [row.key]: e.target.value })}
            />
            <small>Padrão global: {String(row.default)}{row.value !== null ? ' · sobrescrito neste álbum' : ''}</small>
          </label>
        ))}
        <label className="field">
          <span>Simular: % dos pacotes que o jogador abre</span>
          <input type="number" min={0} max={100} value={scenario.open_fraction} onChange={(e) => setScenario({ ...scenario, open_fraction: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>Simular: visitas ao app por dia</span>
          <input type="number" min={0.1} max={48} step="any" value={scenario.visits_per_day} onChange={(e) => setScenario({ ...scenario, visits_per_day: Number(e.target.value) || 1 })} />
        </label>
      </div>
      {!closed ? (
        <div className="admin-actions">
          <button className="btn btn-primary" disabled={!changed.length} onClick={save}>Salvar parâmetros{changed.length ? ` (${changed.length})` : ''}</button>
          {changed.length ? <button className="btn" onClick={() => setDraft(Object.fromEntries(rows.map((row) => [row.key, String(row.effective)])))}>Desfazer</button> : null}
        </div>
      ) : null}
      {result ? <Estimate result={result} /> : null}
    </section>
  );
}

export default function AdminAlbumsPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [editing, setEditing] = useState<Row | 'new' | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => api<Row[]>('/admin/albums').then(setRows).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);

  const duplicate = async (row: Row) => {
    const title = window.prompt('Título do novo álbum (cópia de capítulos, figurinhas, pacotes e parâmetros; sem jogadores):', `${row.title} (cópia)`);
    if (!title) return;
    setError('');
    try {
      // Nasce como rascunho, logo depois do álbum de origem; as datas se ajustam ao editar.
      const starts = new Date(Math.max(Date.parse(row.ends_at), Date.now() + 86400000));
      const ends = new Date(starts.getTime() + (Date.parse(row.ends_at) - Date.parse(row.starts_at)));
      const created = await post<Row>(`/admin/albums/${row.id}/duplicate`, { title, starts_at: starts.toISOString(), ends_at: ends.toISOString() });
      await load();
      setOpenId(created.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const remove = async (row: Row) => {
    if (!window.confirm(`Apagar o álbum "${row.title}" com tudo o que é dele? Isso não pode ser desfeito.`)) return;
    setError('');
    try {
      await api(`/admin/albums/${row.id}`, { method: 'DELETE' });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const open = rows.find((row) => row.id === openId) ?? null;

  return (
    <div className="admin">
      <header className="admin-top">
        <h1>Álbuns</h1>
        <Link to="/admin">Painel admin</Link>
      </header>
      <section>
        <div className="admin-head">
          <h2>Álbuns ({rows.length})</h2>
          <button className="btn" onClick={() => setEditing('new')}>Novo álbum</button>
        </div>
        <Message>{error}</Message>
        {editing ? (
          <AlbumForm
            key={editing === 'new' ? 'new' : editing.id}
            initial={editing === 'new' ? null : editing}
            onCancel={() => setEditing(null)}
            onSaved={() => { setEditing(null); load(); }}
          />
        ) : null}
        <ul className="admin-list">
          {rows.map((row) => (
            <li key={row.id} className={row.id === openId ? 'is-open' : ''}>
              <span>
                <b>{row.title}</b> <em className={`album-status album-status-${row.status}`}>{STATUS[row.status]}</em>
                <small className="album-dates">{when(row.starts_at)} a {when(row.ends_at)} · {row.chapters} capítulos · {row.stickers} figurinhas · {row.participants} jogadores</small>
              </span>
              <span className="admin-row-actions">
                <button className="link" onClick={() => setOpenId(row.id === openId ? null : row.id)}>{row.id === openId ? 'Fechar' : 'Abrir'}</button>
                {row.status !== 'ended' ? <button className="link" onClick={() => setEditing(row)}>Editar</button> : null}
                <button className="link" onClick={() => duplicate(row)}>Duplicar</button>
                {row.status === 'draft' || row.status === 'scheduled' ? <button className="link danger" onClick={() => remove(row)}>Apagar</button> : null}
              </span>
            </li>
          ))}
        </ul>
        {rows.length ? null : <p className="muted">Nenhum álbum ainda. Crie o primeiro.</p>}
      </section>
      {open ? (
        <>
          <h2>{open.title}</h2>
          <Chapters key={`c${open.id}${open.status}`} album={open} />
          <Params key={`p${open.id}`} album={open} />
        </>
      ) : null}
    </div>
  );
}
