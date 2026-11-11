import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, post, type AssetView, type ChapterContent, type OrgView, type RarityView } from '../api';
import { ContentPreview } from '../components/ContentPreview';
import { MfaError, useMfa } from '../components/Mfa';
import { Message } from '../components/ui';

type Row = Record<string, any>;

const STATUS: Record<string, string> = {
  draft: 'Rascunho', submitted: 'Enviada', approved: 'Aprovada', rejected: 'Rejeitada', published: 'Publicada', superseded: 'Substituída',
};
const ALBUM: Record<string, string> = { draft: 'rascunho', scheduled: 'agendado', active: 'em andamento', ended: 'encerrado' };
const FIELD: Record<string, string> = {
  name: 'Nome', size: 'Tamanho', tag: 'Categoria', hint: 'Dica', back_text: 'Verso', back_links: 'Links', page_no: 'Página',
  slot: 'Posição', active: 'Ativa', suggested_rarity_id: 'Raridade sugerida', image: 'Imagem', title: 'Título', text: 'Texto',
  layout: 'Layout', background: 'Fundo',
};
const ACTION: Record<string, string> = {
  'role.grant': 'Papel concedido', 'role.revoke': 'Papel revogado', 'revision.submit': 'Revisão enviada',
  'revision.approve': 'Revisão aprovada', 'revision.reject': 'Revisão rejeitada', 'revision.publish': 'Revisão publicada',
  'revision.rollback': 'Rollback', 'asset.upload': 'Imagem enviada', 'asset.delete': 'Imagem apagada',
  'admin.chapter_create': 'Capítulo criado',
};
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');
const show = (value: unknown) => (value === null || value === undefined || value === '' ? '(vazio)' : typeof value === 'object' ? JSON.stringify(value) : String(value));

function Change({ label, change }: { label: string; change: { old: any; new: any } }) {
  const image = label === 'image' || label === 'background';
  return (
    <li className="diff-field">
      <b>{FIELD[label] ?? label}</b>
      {image ? (
        <span className="diff-images">
          {change.old ? <img src={change.old} alt="Antes" /> : <i>sem imagem</i>}
          <span aria-hidden="true">→</span>
          {change.new ? <img src={change.new} alt="Depois" /> : <i>sem imagem</i>}
        </span>
      ) : (
        <span><del>{show(change.old)}</del> <ins>{show(change.new)}</ins></span>
      )}
    </li>
  );
}

function Diff({ diff }: { diff: Row }) {
  const empty = !diff.added.length && !diff.removed.length && !diff.changed.length && !diff.pages.length && !diff.mosaic && !diff.orientation;
  if (empty) return <p className="muted">Nenhuma diferença em relação ao que está publicado.</p>;
  return (
    <div className="diff">
      {diff.orientation ? <p><b>Orientação:</b> <del>{diff.orientation.old}</del> <ins>{diff.orientation.new}</ins></p> : null}
      {diff.added.length ? (
        <div>
          <h4>Figurinhas novas ({diff.added.length})</h4>
          <ul className="diff-cards">{diff.added.map((s: Row) => <li key={s.key} className="diff-added">{s.image_url ? <img src={s.image_url} alt="" /> : <i />}<span>{s.name}<small>página {s.page_no}</small></span></li>)}</ul>
        </div>
      ) : null}
      {diff.removed.length ? (
        <div>
          <h4>Figurinhas removidas ({diff.removed.length})</h4>
          <ul className="diff-cards">{diff.removed.map((s: Row) => <li key={s.key} className="diff-removed">{s.image_url ? <img src={s.image_url} alt="" /> : <i />}<span>{s.name}<small>página {s.page_no}</small></span></li>)}</ul>
        </div>
      ) : null}
      {diff.changed.length ? (
        <div>
          <h4>Figurinhas alteradas ({diff.changed.length})</h4>
          {diff.changed.map((s: Row) => (
            <details key={s.key} open={diff.changed.length <= 6}>
              <summary>{s.name} · {Object.keys(s.fields).map((f) => FIELD[f] ?? f).join(', ')}</summary>
              <ul>{Object.entries(s.fields).map(([field, change]) => <Change key={field} label={field} change={change as any} />)}</ul>
            </details>
          ))}
        </div>
      ) : null}
      {diff.pages.length ? (
        <div>
          <h4>Páginas</h4>
          {diff.pages.map((page: Row) => (
            <details key={page.page_no} open>
              <summary>Página {page.page_no} {page.kind === 'added' ? '(nova)' : page.kind === 'removed' ? '(removida)' : ''}</summary>
              <ul>{Object.entries(page.fields).map(([field, change]) => <Change key={field} label={field} change={change as any} />)}</ul>
            </details>
          ))}
        </div>
      ) : null}
      {diff.mosaic ? (
        <div>
          <h4>Mosaico</h4>
          <ul>
            <Change label="image" change={{ old: diff.mosaic.old?.image_url ?? null, new: diff.mosaic.new?.image_url ?? null }} />
            <li className="diff-field"><b>Grade</b><span><del>{diff.mosaic.old ? `${diff.mosaic.old.cols}x${diff.mosaic.old.rows}` : '(sem mosaico)'}</del> <ins>{diff.mosaic.new ? `${diff.mosaic.new.cols}x${diff.mosaic.new.rows}` : '(sem mosaico)'}</ins></span></li>
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Detail({ id, rarities, onChanged, onClose }: { id: number; rarities: RarityView[]; onChanged: () => void; onClose: () => void }) {
  const [data, setData] = useState<Row | null>(null);
  const [history, setHistory] = useState<Row[]>([]);
  const [trail, setTrail] = useState<Row[]>([]);
  const [note, setNote] = useState('');
  const [overrides, setOverrides] = useState<Record<string, { rarity_id?: number; number?: number }>>({});
  const [error, setError] = useState<Error | null>(null);
  const [ok, setOk] = useState('');
  const { run, prompt } = useMfa();

  const load = useCallback(async () => {
    const detail = await api<Row>(`/admin/revisions/${id}`);
    setData(detail);
    setHistory(await api<Row[]>(`/admin/revisions?chapter_id=${detail.chapter_id}`));
    setTrail(await api<Row[]>(`/admin/chapters/${detail.chapter_id}/audit`));
  }, [id]);
  useEffect(() => {
    setData(null);
    setOverrides({});
    setNote('');
    load().catch(setError);
  }, [load]);

  const act = async (action: () => Promise<unknown>, message: string) => {
    setError(null);
    setOk('');
    try {
      await run(action);
      setOk(message);
      await load();
      onChanged();
    } catch (err) {
      setError(err as Error);
    }
  };
  if (!data) return <section>{error ? <MfaError error={error} /> : <p className="muted">Carregando revisão…</p>}</section>;

  const content = data.content as ChapterContent;
  const published = data.published as ChapterContent;
  const org = data.org as OrgView;
  const assets = data.assets as AssetView[];
  const numbers = Object.fromEntries(Object.entries(data.published_meta as Record<string, Row>).map(([key, meta]) => [key, meta.number]));
  const added: Row[] = data.diff.added;
  const frozen = data.album_status === 'active';
  const setOverride = (key: string, field: 'rarity_id' | 'number', raw: string) =>
    setOverrides((all) => {
      const next = { ...(all[key] ?? {}) };
      if (raw === '') delete next[field];
      else next[field] = Number(raw);
      return { ...all, [key]: next };
    });
  const publishBody = { stickers: Object.fromEntries(Object.entries(overrides).filter(([, value]) => Object.keys(value).length)) };

  return (
    <section className="review-detail">
      {prompt}
      <div className="admin-head">
        <h2>{data.org_name} · revisão {data.number} <em className={`rev-status rev-${data.status}`}>{STATUS[data.status]}</em></h2>
        <button className="btn" onClick={onClose}>Fechar</button>
      </div>
      <p className="muted">
        Álbum {data.album.title} ({ALBUM[data.album_status]}) · autor: {data.author ?? '—'} · enviada {when(data.submitted_at)}
        {data.review_note ? ` · nota: ${data.review_note}` : ''}
      </p>
      <MfaError error={error} />
      {ok ? <Message kind="ok">{ok}</Message> : null}
      {data.structural.length ? (
        <p className={`estimate-alert ${frozen ? 'estimate-error' : 'estimate-warning'}`} role="alert">
          {frozen ? 'Não pode ser publicada com o álbum em andamento: ' : 'Mexe na estrutura do álbum: '}{data.structural.join('; ')}.
        </p>
      ) : null}

      <h3>O que muda</h3>
      <Diff diff={data.diff} />

      <h3>Prévia</h3>
      <div className="preview-pair">
        <ContentPreview label="Publicado hoje" content={published} org={org} assets={assets} rarities={rarities} numbers={numbers} />
        <ContentPreview label="Proposto" content={content} org={org} assets={assets} rarities={rarities} numbers={numbers} />
      </div>

      {data.status === 'approved' && added.length && !frozen ? (
        <>
          <h3>Raridade e número das figurinhas novas</h3>
          <p className="muted">Em branco: raridade sugerida pelo editor (ou a mais comum) e o próximo número livre do álbum.</p>
          <ul className="admin-list">
            {added.map((sticker) => {
              const suggested = content.stickers.find((s) => s.key === sticker.key)?.suggested_rarity_id;
              return (
                <li key={sticker.key}>
                  <span>{sticker.name}</span>
                  <span className="admin-row-actions">
                    <select aria-label={`Raridade de ${sticker.name}`} value={overrides[sticker.key]?.rarity_id ?? ''} onChange={(e) => setOverride(sticker.key, 'rarity_id', e.target.value)}>
                      <option value="">{suggested ? `Sugerida: ${rarities.find((r) => r.id === suggested)?.name ?? suggested}` : 'A mais comum'}</option>
                      {rarities.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                    </select>
                    <input aria-label={`Número de ${sticker.name}`} type="number" min={1} placeholder="nº automático" value={overrides[sticker.key]?.number ?? ''} onChange={(e) => setOverride(sticker.key, 'number', e.target.value)} />
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      <div className="review-actions">
        {data.status === 'submitted' ? (
          <>
            <label className="field"><span>Nota para o editor (obrigatória ao rejeitar)</span>
              <textarea rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
            <div className="admin-actions">
              <button className="btn btn-primary" onClick={() => act(() => post(`/admin/revisions/${id}/approve`, { note }), 'Revisão aprovada. Agora pode ser publicada.')}>Aprovar</button>
              <button className="btn" onClick={() => act(() => post(`/admin/revisions/${id}/reject`, { note }), 'Revisão rejeitada; voltou para o editor.')}>Rejeitar</button>
            </div>
          </>
        ) : null}
        {data.status === 'approved' ? (
          <div className="admin-actions">
            <button className="btn btn-primary" onClick={() => window.confirm('Publicar esta revisão? O conteúdo passa a valer para os jogadores.') && act(() => post(`/admin/revisions/${id}/publish`, publishBody), 'Publicada.')}>Publicar</button>
          </div>
        ) : null}
        {data.can_rollback ? (
          <div className="admin-actions">
            <button className="btn" onClick={() => window.confirm('Desfazer esta publicação e voltar ao conteúdo anterior?') && act(() => post(`/admin/revisions/${id}/rollback`), 'Publicação desfeita: o conteúdo anterior voltou.')}>Rollback</button>
          </div>
        ) : null}
      </div>

      <h3>Histórico do capítulo</h3>
      <ul className="admin-list">
        {history.map((row) => (
          <li key={row.id}>
            <span>Revisão {row.number} · {STATUS[row.status]}{row.is_live ? ' · no ar' : ''}</span>
            <small>{row.author ?? '—'} · {when(row.published_at ?? row.submitted_at ?? row.updated_at)}</small>
          </li>
        ))}
      </ul>
      <h3>Auditoria do capítulo</h3>
      <ul className="admin-list">
        {trail.slice(0, 30).map((row) => (
          <li key={row.id}><span>{ACTION[row.action] ?? row.action}</span><small>{row.by ?? 'sistema'} · {when(row.at)}</small></li>
        ))}
      </ul>
    </section>
  );
}

function Editors({ rows, reload }: { rows: Row[]; reload: () => void }) {
  const [email, setEmail] = useState<Record<number, string>>({});
  const [error, setError] = useState<Error | null>(null);
  const { run, prompt } = useMfa();
  const guard = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await run(action);
      reload();
    } catch (err) {
      setError(err as Error);
    }
  };
  const grant = (event: FormEvent, chapterId: number) => {
    event.preventDefault();
    guard(async () => {
      await post(`/admin/chapters/${chapterId}/editors`, { email: email[chapterId] });
      setEmail((all) => ({ ...all, [chapterId]: '' }));
    });
  };
  return (
    <section>
      {prompt}
      <h2>Orgs e editores</h2>
      <p className="muted">Editor só escreve rascunho do próprio capítulo. Revogar vale na hora.</p>
      <MfaError error={error} />
      <ul className="admin-list editors">
        {rows.map((row) => (
          <li key={row.chapter_id}>
            <span>
              <i className="dot" style={{ background: row.org_color }} /> <b>{row.org_name}</b>
              <small className="album-dates">
                {row.album_title} ({ALBUM[row.album_status]}) · {row.revision ? `revisão ${row.revision.number}: ${STATUS[row.revision.status]}` : 'sem revisão em aberto'} · última edição {when(row.last_edit_at)}
              </small>
            </span>
            <span className="editor-list">
              {row.editors.length ? row.editors.map((editor: Row) => (
                <span key={editor.grant_id} className="editor-chip">
                  {editor.nickname} <small>{editor.email}</small>
                  <button className="link danger" onClick={() => window.confirm(`Tirar ${editor.nickname} de ${row.org_name}?`) && guard(() => api(`/admin/roles/${editor.grant_id}`, { method: 'DELETE' }))}>Revogar</button>
                </span>
              )) : <small className="muted">Sem editores</small>}
              {row.album_status !== 'ended' ? (
                <form className="inline-form" onSubmit={(event) => grant(event, row.chapter_id)}>
                  <input type="email" required aria-label={`E-mail do novo editor de ${row.org_name}`} placeholder="e-mail da conta" value={email[row.chapter_id] ?? ''} onChange={(e) => setEmail({ ...email, [row.chapter_id]: e.target.value })} />
                  <button className="btn">Tornar editor</button>
                </form>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      {rows.length ? null : <p className="muted">Nenhum capítulo ainda. Crie em Álbuns.</p>}
    </section>
  );
}

const QUEUES = ['submitted', 'approved', 'rejected', 'published'] as const;

export default function AdminReviewPage() {
  const [queue, setQueue] = useState<(typeof QUEUES)[number]>('submitted');
  const [rows, setRows] = useState<Row[]>([]);
  const [overview, setOverview] = useState<Row[]>([]);
  const [rarities, setRarities] = useState<RarityView[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    api<Row[]>(`/admin/revisions?status=${queue}`).then(setRows).catch((e) => setError(e.message));
    api<Row[]>('/admin/editors/overview').then(setOverview).catch(() => {});
  }, [queue]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    api<RarityView[]>('/admin/rarities').then((list) => setRarities([...list].sort((a, b) => a.rank - b.rank))).catch(() => {});
  }, []);

  return (
    <div className="admin">
      <header className="admin-top">
        <h1>Revisões</h1>
        <Link to="/admin">Painel admin</Link>
      </header>
      <section>
        <nav className="admin-tabs" aria-label="Fila de revisões">
          {QUEUES.map((name) => <button key={name} className={queue === name ? 'active' : ''} onClick={() => { setQueue(name); setOpenId(null); }}>{STATUS[name]}s</button>)}
        </nav>
        <Message>{error}</Message>
        <ul className="admin-list">
          {rows.map((row) => (
            <li key={row.id} className={row.id === openId ? 'is-open' : ''}>
              <span><i className="dot" style={{ background: row.org_color }} /> <b>{row.org_name}</b> · revisão {row.number}
                <small className="album-dates">{row.album.title} · {row.author ?? '—'} · {when(row.submitted_at ?? row.updated_at)}{row.is_live ? ' · no ar' : ''}</small>
              </span>
              <button className="link" onClick={() => setOpenId(row.id === openId ? null : row.id)}>{row.id === openId ? 'Fechar' : 'Revisar'}</button>
            </li>
          ))}
        </ul>
        {rows.length ? null : <p className="muted">Nada nesta fila.</p>}
      </section>
      {openId ? <Detail key={openId} id={openId} rarities={rarities} onChanged={load} onClose={() => setOpenId(null)} /> : null}
      <Editors rows={overview} reload={load} />
    </div>
  );
}
