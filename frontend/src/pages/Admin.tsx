import QRCode from 'qrcode';
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, post } from '../api';
import { Message } from '../components/ui';

type Row = Record<string, any>;
interface FieldDef {
  key: string;
  label: string;
  type?: 'text' | 'number' | 'color' | 'textarea' | 'json' | 'image' | 'checkbox' | 'select';
  options?: { value: string | number; label: string }[];
  hint?: string;
  optional?: boolean;
  uploadName?: (row: Row) => string; // pasta e nome legíveis no bucket
}

function FieldInput({ field, value, row = {}, onChange }: { field: FieldDef; value: any; row?: Row; onChange: (v: any) => void }) {
  const [note, setNote] = useState('');
  const type = field.type ?? 'text';
  if (type === 'textarea') return <textarea rows={3} value={value ?? ''} onChange={(e) => onChange(e.target.value)} />;
  if (type === 'json') return <textarea rows={3} className="mono" value={value ?? ''} onChange={(e) => onChange(e.target.value)} />;
  if (type === 'checkbox') return <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  if (type === 'select') {
    return (
      <select value={value ?? ''} required={!field.optional} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}>
        <option value="">{field.optional ? 'Nenhum' : 'Escolha…'}</option>
        {field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    );
  }
  if (type === 'image') {
    const upload = async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      form.append('name', field.uploadName?.(row) ?? 'uploads/imagem');
      setNote('Enviando…');
      try {
        const result = await api<{ url: string }>('/admin/upload', { form });
        onChange(result.url);
        setNote('');
      } catch (error) {
        setNote((error as Error).message);
      }
    };
    return (
      <div className="image-field">
        {value ? <img src={value} alt="" /> : null}
        <input type="file" accept="image/png,image/webp,image/jpeg" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        {note ? <small>{note}</small> : null}
      </div>
    );
  }
  return (
    <input
      type={type}
      required={!field.optional}
      step={type === 'number' ? 'any' : undefined}
      value={value ?? ''}
      onChange={(e) => onChange(type === 'number' ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value)}
    />
  );
}

function toForm(fields: FieldDef[], row: Row) {
  const values: Row = {};
  for (const field of fields) {
    const value = row[field.key];
    values[field.key] = field.type === 'json' ? JSON.stringify(value ?? (field.key === 'back_links' ? [] : {}), null, 1) : value;
  }
  return values;
}

function CrudSection({ title, path, fields, columns, defaults, extraRowAction }: { title: string; path: string; fields: FieldDef[]; columns: (row: Row) => string; defaults: Row; extraRowAction?: (row: Row, reload: () => void) => React.ReactNode }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [editing, setEditing] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => api<Row[]>(path).then(setRows).catch((e) => setError(e.message)), [path]);
  useEffect(() => {
    load();
  }, [load]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    try {
      const body: Row = {};
      for (const field of fields) {
        const value = editing![field.key];
        if (field.type === 'json') body[field.key] = JSON.parse(value || 'null');
        else if (value !== undefined && value !== '') body[field.key] = value;
      }
      if (editing!.id) await api(`${path}/${editing!.id}`, { method: 'PUT', body });
      else await post(path, body);
      setEditing(null);
      load();
    } catch (e) {
      setError(e instanceof SyntaxError ? 'Um dos campos JSON está com erro de sintaxe.' : (e as Error).message);
    }
  };

  const remove = async (row: Row) => {
    if (!window.confirm(`Apagar "${columns(row)}"? Isso não pode ser desfeito.`)) return;
    try {
      await api(`${path}/${row.id}`, { method: 'DELETE' });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section>
      <div className="admin-head">
        <h2>{title} ({rows.length})</h2>
        <button className="btn" onClick={() => setEditing(toForm(fields, defaults))}>Novo</button>
      </div>
      <Message>{error}</Message>
      {editing ? (
        <form className="admin-form" onSubmit={save}>
          {fields.map((field) => (
            <label key={field.key} className="field">
              <span>{field.label}</span>
              <FieldInput field={field} value={editing[field.key]} row={editing} onChange={(value) => setEditing({ ...editing, [field.key]: value })} />
              {field.hint ? <small>{field.hint}</small> : null}
            </label>
          ))}
          <div className="admin-actions">
            <button className="btn btn-primary">Salvar</button>
            <button type="button" className="btn" onClick={() => setEditing(null)}>Cancelar</button>
          </div>
        </form>
      ) : null}
      <ul className="admin-list">
        {rows.map((row) => (
          <li key={row.id}>
            <span>{columns(row)}</span>
            <span className="admin-row-actions">
              {extraRowAction?.(row, load)}
              <button className="link" onClick={() => setEditing({ ...toForm(fields, row), id: row.id })}>Editar</button>
              <button className="link danger" onClick={() => remove(row)}>Apagar</button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function useOptions(path: string, label: (row: Row) => string) {
  const [options, setOptions] = useState<{ value: number; label: string }[]>([]);
  useEffect(() => {
    api<Row[]>(path).then((rows) => setOptions(rows.map((row) => ({ value: row.id, label: label(row) })))).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return options;
}

function Metrics() {
  const [data, setData] = useState<Row | null>(null);
  useEffect(() => {
    api('/admin/metrics').then(setData).catch(() => {});
  }, []);
  if (!data) return null;
  const tiles: [string, string | number][] = [
    ['Usuários', data.users],
    ['Confirmados', data.verified_users],
    ['Pacotes abertos', data.packs_opened],
    ['Trocas concluídas', data.trades_done],
    ['Álbum médio', `${data.avg_album_percent}%`],
  ];
  return (
    <section>
      <div className="metric-grid">
        {tiles.map(([label, value]) => (
          <div key={label} className="metric"><strong>{value}</strong><span>{label}</span></div>
        ))}
      </div>
      <h3>Ranking de progresso</h3>
      {data.ranking.length ? (
        <ol className="ranking">
          {data.ranking.map((row: Row) => <li key={row.nickname}><span>{row.nickname}</span><span>{row.pasted} ({row.percent}%)</span></li>)}
        </ol>
      ) : <p className="muted">Ninguém colou figurinhas ainda.</p>}
    </section>
  );
}

/** Folha imprimível: um cartão por código, com o QR do link de resgate e o texto ao lado. */
function CodeSheet({ codes, onClose }: { codes: { code: string; label: string }[]; onClose: () => void }) {
  const [images, setImages] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    Promise.all(
      codes.map(async ({ code }) => [code, await QRCode.toDataURL(`${window.location.origin}/r/${code}`, { width: 240, margin: 1 })] as const),
    ).then((pairs) => alive && setImages(Object.fromEntries(pairs)));
    return () => {
      alive = false;
    };
  }, [codes]);
  useEffect(() => {
    document.body.classList.add('printing-codes');
    return () => document.body.classList.remove('printing-codes');
  }, []);
  const ready = Object.keys(images).length === codes.length;
  return (
    <div className="code-sheet">
      <div className="code-sheet-bar">
        <strong>{codes.length} {codes.length === 1 ? 'código' : 'códigos'} para imprimir</strong>
        <button className="btn btn-primary" disabled={!ready} onClick={() => window.print()}>Imprimir</button>
        <button className="btn" onClick={onClose}>Fechar</button>
      </div>
      <ul className="code-cards">
        {codes.map(({ code, label }) => (
          <li key={code}>
            {images[code] ? <img src={images[code]} alt={`QR do código ${code}`} /> : <i />}
            <div>
              <small>Aponte a câmera ou digite no app</small>
              <code>{code}</code>
              {label ? <span>{label}</span> : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Codes() {
  const [sheet, setSheet] = useState<{ code: string; label: string }[] | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [form, setForm] = useState<Row>({ pack_type_id: null, label: '', count: 10, expires_at: '' });
  const [error, setError] = useState('');
  const packs = useOptions('/admin/pack-types', (row) => row.name);
  const load = useCallback(() => api<Row[]>('/admin/codes').then(setRows).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);
  const create = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    try {
      const created = await post<{ codes: string[] }>('/admin/codes', { ...form, expires_at: form.expires_at ? new Date(form.expires_at).toISOString() : null });
      load();
      setSheet(created.codes.map((code) => ({ code, label: form.label })));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const available = rows.filter((row) => !row.redeemed_by).map((row) => ({ code: row.code as string, label: (row.label as string) || '' }));
  if (sheet) return <CodeSheet codes={sheet} onClose={() => setSheet(null)} />;
  return (
    <section>
      <h2>Gerar lote de códigos</h2>
      <form className="admin-form" onSubmit={create}>
        <label className="field"><span>Tipo de pacote</span><FieldInput field={{ key: 'p', label: '', type: 'select', options: packs }} value={form.pack_type_id} onChange={(v) => setForm({ ...form, pack_type_id: v })} /></label>
        <label className="field"><span>Rótulo (ex.: biblioteca)</span><input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
        <label className="field"><span>Quantidade</span><input type="number" min={1} max={1000} value={form.count} onChange={(e) => setForm({ ...form, count: Number(e.target.value) })} /></label>
        <label className="field"><span>Validade (opcional)</span><input type="datetime-local" value={form.expires_at} onChange={(e) => setForm({ ...form, expires_at: e.target.value })} /></label>
        <div className="admin-actions"><button className="btn btn-primary">Gerar</button></div>
      </form>
      <Message>{error}</Message>
      <div className="admin-head">
        <h3>Códigos ({rows.length})</h3>
        {available.length ? <button className="btn" onClick={() => setSheet(available)}>Imprimir disponíveis com QR ({available.length})</button> : null}
      </div>
      <ul className="admin-list codes">
        {rows.map((row) => (
          <li key={row.id}>
            <code>{row.code}</code>
            <span>{row.label || 'sem rótulo'} · {row.pack_type}</span>
            <span className={row.redeemed_by ? 'used' : 'muted'}>
              {row.redeemed_by ? `${row.redeemed_by}, ${new Date(row.redeemed_at).toLocaleString('pt-BR')}` : 'Disponível'}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ConfigEditor() {
  const [rows, setRows] = useState<Row[]>([]);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    api<Row[]>('/admin/config').then(setRows).catch((e) => setError(e.message));
  }, []);
  const save = async (key: string, raw: string, original: unknown) => {
    setSaved('');
    setError('');
    try {
      const value = typeof original === 'number' ? Number(raw) : raw;
      await api(`/admin/config/${key}`, { method: 'PUT', body: { value } });
      setRows((all) => all.map((row) => (row.key === key ? { ...row, value } : row)));
      setSaved(`${key} salvo.`);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section>
      <h2>Parâmetros</h2>
      <p className="muted">Mudanças valem na hora, sem mexer no código.</p>
      <Message>{error}</Message>
      {saved ? <Message kind="ok">{saved}</Message> : null}
      <div className="config-list">
        {rows.map((row) => (
          <label key={row.key} className="field">
            <span className="mono">{row.key}</span>
            <input
              key={String(row.value)}
              type={typeof row.value === 'number' ? 'number' : 'text'}
              defaultValue={row.value}
              onBlur={(e) => e.target.value !== String(row.value) && save(row.key, e.target.value, row.value)}
            />
          </label>
        ))}
      </div>
    </section>
  );
}

const TABS = ['Métricas', 'Orgs', 'Figurinhas', 'Pacotes', 'Raridades', 'Códigos', 'Parâmetros'] as const;

export default function AdminPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Métricas');
  const orgs = useOptions('/admin/orgs', (row) => row.name);
  const rarities = useOptions('/admin/rarities', (row) => row.name);
  const rarityHint = rarities.map((r) => `${r.value} = ${r.label}`).join(', ');

  return (
    <div className="admin">
      <header className="admin-top">
        <h1>Painel admin</h1>
        <Link to="/">Voltar ao álbum</Link>
      </header>
      <nav className="admin-tabs">
        {TABS.map((name) => <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}>{name}</button>)}
      </nav>
      {tab === 'Métricas' ? <Metrics /> : null}
      {tab === 'Orgs' ? (
        <CrudSection
          title="Orgs"
          path="/admin/orgs"
          columns={(row) => row.name}
          defaults={{ theme_color: '#1d4ed8', sort_order: 0 }}
          fields={[
            { key: 'name', label: 'Nome' },
            { key: 'description', label: 'Apresentação', type: 'textarea', optional: true },
            { key: 'logo_url', label: 'Logo', type: 'image', optional: true, uploadName: (row) => `orgs/${row.name || 'sem-nome'}/logo` },
            { key: 'theme_color', label: 'Cor tema', type: 'color' },
            { key: 'sort_order', label: 'Ordem', type: 'number' },
            { key: 'mosaic_image_url', label: 'Imagem do mosaico', type: 'image', optional: true, uploadName: (row) => `orgs/${row.name || 'sem-nome'}/mosaico` },
          ]}
          extraRowAction={(row) => (
            <button
              className="link"
              onClick={async () => {
                const shape = window.prompt('Colunas x linhas do mosaico. As peças atuais serão substituídas.', `${row.mosaic_cols}x${row.mosaic_rows}`);
                const match = shape?.match(/^(\d+)\s*x\s*(\d+)$/);
                if (!match) return;
                try {
                  const result = await post(`/admin/orgs/${row.id}/mosaic`, { cols: Number(match[1]), rows: Number(match[2]), rarity_id: rarities[0]?.value });
                  window.alert(`${result.pieces} peças geradas.`);
                } catch (e) {
                  window.alert((e as Error).message);
                }
              }}
            >
              Mosaico
            </button>
          )}
        />
      ) : null}
      {tab === 'Figurinhas' ? (
        <CrudSection
          title="Figurinhas"
          path="/admin/stickers"
          columns={(row) => `#${row.number} ${row.name}${row.active ? '' : ' (inativa)'}`}
          defaults={{ size: 'P', tag: 'membro', page_no: 1, active: true, back_links: [], slot_layout: { x: 5, y: 15, w: 28, h: 27 } }}
          fields={[
            { key: 'org_id', label: 'Org', type: 'select', options: orgs },
            { key: 'number', label: 'Número', type: 'number', optional: true, hint: 'Vazio usa o próximo livre.' },
            { key: 'name', label: 'Nome' },
            {
              key: 'image_url',
              label: 'Imagem',
              type: 'image',
              optional: true,
              uploadName: (row) =>
                `orgs/${orgs.find((org) => org.value === row.org_id)?.label ?? 'sem-org'}/figurinhas/${row.number ? String(row.number).padStart(3, '0') : ''} ${row.name ?? ''}`,
            },
            { key: 'size', label: 'Tamanho (P, M ou G)' },
            { key: 'rarity_id', label: 'Raridade', type: 'select', options: rarities },
            { key: 'tag', label: 'Categoria (membro, pet, gestão…)' },
            { key: 'hint', label: 'Dica no espaço vazio', optional: true },
            { key: 'back_text', label: 'Texto do verso', type: 'textarea', optional: true },
            { key: 'back_links', label: 'Links do verso', type: 'json', hint: '[{"label": "Instagram", "url": "https://..."}]' },
            { key: 'page_no', label: 'Página da org', type: 'number' },
            { key: 'slot_layout', label: 'Posição do espaço (%)', type: 'json', hint: '{"x": 5, "y": 15, "w": 28, "h": 27}' },
            { key: 'active', label: 'Ativa', type: 'checkbox', optional: true },
          ]}
        />
      ) : null}
      {tab === 'Pacotes' ? (
        <CrudSection
          title="Tipos de pacote"
          path="/admin/pack-types"
          columns={(row) => `${row.name}: ${row.stickers_per_pack} figurinhas`}
          defaults={{ stickers_per_pack: 6, rarity_weights: {}, config: {} }}
          fields={[
            { key: 'slug', label: 'Identificador', hint: 'Usado nos parâmetros de concessão diária.' },
            { key: 'name', label: 'Nome' },
            { key: 'stickers_per_pack', label: 'Figurinhas por pacote', type: 'number' },
            { key: 'rarity_weights', label: 'Pesos de raridade', type: 'json', hint: `{"id": peso}. Ids: ${rarityHint}. Ausente usa o peso geral.` },
            { key: 'guaranteed_min_rarity', label: 'Garantia mínima', type: 'select', options: rarities, optional: true },
            { key: 'org_filter', label: 'Só desta org', type: 'select', options: orgs, optional: true },
          ]}
        />
      ) : null}
      {tab === 'Raridades' ? (
        <CrudSection
          title="Raridades"
          path="/admin/rarities"
          columns={(row) => `${row.name}: peso ${row.weight}`}
          defaults={{ rank: 4, weight: 1, color: '#888888' }}
          fields={[
            { key: 'name', label: 'Nome' },
            { key: 'rank', label: 'Nível (maior = mais rara)', type: 'number' },
            { key: 'weight', label: 'Peso geral', type: 'number' },
            { key: 'color', label: 'Cor', type: 'color' },
          ]}
        />
      ) : null}
      {tab === 'Códigos' ? <Codes /> : null}
      {tab === 'Parâmetros' ? <ConfigEditor /> : null}
    </div>
  );
}
