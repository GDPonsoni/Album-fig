import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, post, type AssetView, type ChapterContent, type OrgView, type RarityView } from '../api';
import { ContentPreview } from '../components/ContentPreview';
import { useAuth } from '../auth';
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
  layout: 'Layout', background: 'Fundo', description: 'Descrição', color: 'Cor',
};
const ACTION: Record<string, string> = {
  'role.grant': 'Papel concedido', 'role.revoke': 'Papel revogado', 'revision.submit': 'Revisão enviada', 'revision.edit': 'Rascunho editado', 'promo.codes_create': 'Códigos gerados', 'revision.withdraw': 'Envio retirado',
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
  const empty = !diff.added.length && !diff.removed.length && !diff.changed.length && !diff.pages.length && !diff.mosaic && !diff.mosaic_pieces?.length && !diff.orientation && !diff.org;
  if (empty) return <p className="muted">Nenhuma diferença em relação ao que está publicado.</p>;
  return (
    <div className="diff">
      {diff.org ? (
        <div>
          <h4>Organização</h4>
          <ul>{Object.entries(diff.org).map(([field, change]) => <Change key={field} label={field === 'logo' ? 'image' : field} change={change as { old: any; new: any }} />)}</ul>
        </div>
      ) : null}
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
      {diff.mosaic_pieces?.length ? (
        <div>
          <h4>Peças do mosaico ({diff.mosaic_pieces.length})</h4>
          {diff.mosaic_pieces.map((piece: Row) => (
            <details key={piece.index} open={diff.mosaic_pieces.length <= 4}>
              <summary>Peça {piece.index + 1} · {Object.keys(piece.fields).map((f) => FIELD[f] ?? f).join(', ')}</summary>
              <ul>{Object.entries(piece.fields).map(([field, change]) => <Change key={field} label={field} change={change as any} />)}</ul>
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

function Detail({ id, admin, onChanged, onClose }: { id: number; admin: boolean; onChanged: () => void; onClose: () => void }) {
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
    if (admin) setTrail(await api<Row[]>(`/admin/chapters/${detail.chapter_id}/audit`));
  }, [id, admin]);
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
  const rarities: RarityView[] = data.rarities;

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

  const diff = data.diff as Row;
  const counts: [string, number][] = [
    ['figurinhas novas', diff.added.length],
    ['alteradas', diff.changed.length],
    ['removidas', diff.removed.length],
    ['páginas', diff.pages.length],
    ['mosaico', diff.mosaic ? 1 : 0],
    ['peças do mosaico', diff.mosaic_pieces?.length ?? 0],
    ['identidade da org', diff.org ? 1 : 0],
  ];
  const steps = [['submitted', 'Enviada'], ['approved', 'Aprovada'], ['published', 'Publicada']] as const;
  const stepIndex = Math.max(0, data.status === 'rejected' ? 0 : data.status === 'superseded' ? 2 : steps.findIndex(([value]) => value === data.status));
  const nextStep: Record<string, string> = {
    submitted: 'Confira o que muda e a prévia abaixo. "Aprovar e publicar" já coloca no ar; "Só aprovar" deixa para publicar depois. Rejeitar exige uma nota para o editor.',
    approved: 'Aprovada, mas ainda não está no ar. Publique para valer para os jogadores (se há figurinhas novas, defina raridade e número antes).',
    rejected: 'Devolvida ao editor com a sua nota. Nada a fazer até ele corrigir e enviar de novo.',
    published: 'Já está no ar. Se algo deu errado, use "Desfazer publicação" para voltar ao conteúdo anterior.',
    superseded: 'Substituída por uma revisão mais nova.',
    draft: 'O editor ainda está escrevendo.',
  };
  const people = (data.contributors as string[] | undefined)?.length ? (data.contributors as string[]).join(', ') : data.author ?? '—';

  return (
    <section className="review-detail">
      {prompt}
      <div className="admin-head">
        <h2>{data.org_name} · revisão {data.number}</h2>
        <button className="btn" onClick={onClose}>Fechar</button>
      </div>
      <ol className="steps" aria-label="Etapas da revisão">
        {steps.map(([value, label], index) => (
          <li key={value} className={`${index < stepIndex || (index === stepIndex && ['published', 'superseded'].includes(data.status)) ? 'done' : index === stepIndex ? 'now' : ''}${data.status === 'rejected' && index === 0 ? ' bad' : ''}`}>{data.status === 'rejected' && index === 0 ? 'Devolvida' : label}</li>
        ))}
      </ol>
      <p className="muted">
        Álbum {data.album.title} ({ALBUM[data.album_status]}) · escrita por {people} · enviada {when(data.submitted_at)}
        {data.review_note ? ` · nota: ${data.review_note}` : ''}
      </p>
      <p className="next-step">{nextStep[data.status]}</p>
      <MfaError error={error} />
      {ok ? <Message kind="ok">{ok}</Message> : null}
      {data.structural.length ? (
        <p className={`estimate-alert ${frozen ? 'estimate-error' : 'estimate-warning'}`} role="alert">
          {frozen ? 'Não pode ser publicada com o álbum em andamento: ' : 'Mexe na estrutura do álbum: '}{data.structural.join('; ')}.
        </p>
      ) : null}

      <h3>Resumo</h3>
      <ul className="chips">
        {counts.filter(([, n]) => n).map(([label, n]) => <li key={label}><b>{n}</b> {label}</li>)}
        {counts.every(([, n]) => !n) ? <li>Nenhuma mudança</li> : null}
      </ul>

      <h3>Antes e depois</h3>
      <div className="preview-pair">
        <ContentPreview label="Publicado hoje" content={published} org={org} assets={assets} rarities={rarities} numbers={numbers} />
        <ContentPreview label="Proposto" content={content} org={org} assets={assets} rarities={rarities} numbers={numbers} />
      </div>

      <details className="fold" open={counts.reduce((sum, [, n]) => sum + n, 0) <= 8}>
        <summary>Cada mudança em detalhe</summary>
        <Diff diff={diff} />
      </details>

      {['submitted', 'approved'].includes(data.status) && added.length && !frozen ? (
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

      <details className="fold">
        <summary>Histórico e auditoria do capítulo</summary>
        <ul className="admin-list">
          {history.map((row) => (
            <li key={row.id}>
              <span>Revisão {row.number} · {STATUS[row.status]}{row.is_live ? ' · no ar' : ''}</span>
              <small>{(row.contributors?.length ? row.contributors.join(', ') : row.author) ?? '—'} · {when(row.published_at ?? row.submitted_at ?? row.updated_at)}</small>
            </li>
          ))}
        </ul>
        {admin ? (
          <ul className="admin-list">
            {trail.slice(0, 30).map((row) => (
              <li key={row.id}><span>{ACTION[row.action] ?? row.action}</span><small>{row.by ?? 'sistema'} · {when(row.at)}</small></li>
            ))}
          </ul>
        ) : null}
      </details>

      {['submitted', 'approved'].includes(data.status) || data.can_rollback ? (
        <div className="decision-bar">
          {data.status === 'submitted' ? (
            <>
              <label className="field"><span>Nota para o editor <small>(obrigatória ao rejeitar)</small></span>
                <textarea rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="decision-buttons">
                <button className="btn btn-reject" onClick={() => act(() => post(`/admin/revisions/${id}/reject`, { note }), 'Revisão rejeitada; voltou para o editor.')}>Rejeitar</button>
                <button className="btn" onClick={() => act(() => post(`/admin/revisions/${id}/approve`, { note }), 'Revisão aprovada. Falta publicar.')}>Só aprovar</button>
                <button className="btn btn-primary" onClick={() => window.confirm('Aprovar e publicar? O conteúdo passa a valer para os jogadores em instantes.') && act(async () => { await post(`/admin/revisions/${id}/approve`, { note }); await post(`/admin/revisions/${id}/publish`, publishBody); }, 'Aprovada e publicada: os jogadores já veem a mudança.')}>Aprovar e publicar</button>
              </div>
            </>
          ) : null}
          {data.status === 'approved' ? (
            <div className="decision-buttons">
              <button className="btn btn-primary" onClick={() => window.confirm('Publicar esta revisão? O conteúdo passa a valer para os jogadores.') && act(() => post(`/admin/revisions/${id}/publish`, publishBody), 'Publicada.')}>Publicar no álbum</button>
            </div>
          ) : null}
          {data.can_rollback ? (
            <div className="decision-buttons">
              <button className="btn" onClick={() => window.confirm('Desfazer esta publicação e voltar ao conteúdo anterior?') && act(() => post(`/admin/revisions/${id}/rollback`), 'Publicação desfeita: o conteúdo anterior voltou.')}>Desfazer publicação</button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Editors({ rows, reload }: { rows: Row[]; reload: () => void }) {
  const [email, setEmail] = useState<Record<number, string>>({});
  const [role, setRole] = useState<Record<number, 'editor' | 'reviewer'>>({});
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
      await post(`/admin/chapters/${chapterId}/editors`, { email: email[chapterId], role: role[chapterId] ?? 'editor' });
      setEmail((all) => ({ ...all, [chapterId]: '' }));
    });
  };
  return (
    <section>
      {prompt}
      <h2>Orgs, editores e revisores</h2>
      <p className="muted">Editor só escreve rascunho do próprio capítulo. Revisor aprova, rejeita, publica e desfaz as revisões do capítulo (com 2FA) e não julga o que ele mesmo escreveu. Revogar vale na hora.</p>
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
              {row.reviewers.map((reviewer: Row) => (
                <span key={reviewer.grant_id} className="editor-chip">
                  Revisor: {reviewer.nickname} <small>{reviewer.email}</small>
                  <button className="link danger" onClick={() => window.confirm(`Tirar ${reviewer.nickname} da revisão de ${row.org_name}?`) && guard(() => api(`/admin/roles/${reviewer.grant_id}`, { method: 'DELETE' }))}>Revogar</button>
                </span>
              ))}
              {row.album_status !== 'ended' ? (
                <form className="inline-form" onSubmit={(event) => grant(event, row.chapter_id)}>
                  <input type="email" required aria-label={`E-mail do novo editor de ${row.org_name}`} placeholder="e-mail da conta" value={email[row.chapter_id] ?? ''} onChange={(e) => setEmail({ ...email, [row.chapter_id]: e.target.value })} />
                  <select aria-label="Papel" value={role[row.chapter_id] ?? 'editor'} onChange={(e) => setRole({ ...role, [row.chapter_id]: e.target.value as 'editor' | 'reviewer' })}>
                    <option value="editor">Editor</option>
                    <option value="reviewer">Revisor</option>
                  </select>
                  <button className="btn">Conceder</button>
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

function Promoters() {
  const [rows, setRows] = useState<Row[]>([]);
  const [email, setEmail] = useState('');
  const [error, setError] = useState<Error | null>(null);
  const { run, prompt } = useMfa();
  const load = useCallback(() => api<Row[]>('/admin/promoters').then(setRows).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);
  const guard = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await run(action);
      load();
    } catch (err) {
      setError(err as Error);
    }
  };
  return (
    <section>
      {prompt}
      <h2>Promotores</h2>
      <p className="muted">Promotor gera códigos de pacote extra (com QR) dentro da cota diária. Cada código e pacote guarda quem o gerou. Revogar vale na hora.</p>
      <MfaError error={error} />
      <ul className="admin-list">
        {rows.filter((row) => row.state === 'active').map((row) => (
          <li key={row.id}>
            <span><b>{row.nickname}</b> <small className="album-dates">{row.email}</small></span>
            <button className="link danger" onClick={() => window.confirm(`Tirar o papel de promotor de ${row.nickname}?`) && guard(() => api(`/admin/roles/${row.id}`, { method: 'DELETE' }))}>Revogar</button>
          </li>
        ))}
      </ul>
      <form className="inline-form" onSubmit={(event) => { event.preventDefault(); guard(async () => { await post('/admin/promoters', { email }); setEmail(''); }); }}>
        <input type="email" required aria-label="E-mail do novo promotor" placeholder="e-mail da conta" value={email} onChange={(e) => setEmail(e.target.value)} />
        <button className="btn">Tornar promotor</button>
      </form>
    </section>
  );
}

const QUEUES = ['submitted', 'approved', 'rejected', 'published'] as const;
const QUEUE_LABEL: Record<(typeof QUEUES)[number], string> = { submitted: 'Para revisar', approved: 'Para publicar', rejected: 'Devolvidas', published: 'Publicadas' };

export default function AdminReviewPage() {
  const [queue, setQueue] = useState<(typeof QUEUES)[number]>('submitted');
  const [rows, setRows] = useState<Row[]>([]);
  const [overview, setOverview] = useState<Row[]>([]);
  const { user } = useAuth();
  const admin = !!user?.is_admin;
  const [openId, setOpenId] = useState<number | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    api<Row[]>(`/admin/revisions?status=${queue}`).then(setRows).catch((e) => setError(e.message));
    if (admin) api<Row[]>('/admin/editors/overview').then(setOverview).catch(() => {});
  }, [queue, admin]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="admin">
      <header className="admin-top">
        <h1>Revisão de capítulos</h1>
        {admin ? <Link to="/admin">Painel admin</Link> : <Link to="/conta">Minha conta</Link>}
      </header>
      <section>
        <nav className="admin-tabs" aria-label="Fila de revisões">
          {QUEUES.map((name) => <button key={name} className={queue === name ? 'active' : ''} onClick={() => { setQueue(name); setOpenId(null); }}>{QUEUE_LABEL[name]}</button>)}
        </nav>
        <Message>{error}</Message>
        <ul className="admin-list">
          {rows.map((row) => (
            <li key={row.id} className={row.id === openId ? 'is-open' : ''}>
              <span><i className="dot" style={{ background: row.org_color }} /> <b>{row.org_name}</b> · revisão {row.number}
                <small className="album-dates">{row.album.title} · {(row.contributors?.length ? row.contributors.join(', ') : row.author) ?? '—'} · {when(row.submitted_at ?? row.updated_at)}{row.is_live ? ' · no ar' : ''}</small>
              </span>
              <button className="link" onClick={() => setOpenId(row.id === openId ? null : row.id)}>{row.id === openId ? 'Fechar' : 'Revisar'}</button>
            </li>
          ))}
        </ul>
        {rows.length ? null : <p className="muted">{queue === 'submitted' ? 'Nada esperando revisão.' : 'Nada nesta fila.'}</p>}
      </section>
      {openId ? <Detail key={openId} id={openId} admin={admin} onChanged={load} onClose={() => setOpenId(null)} /> : null}
      {admin ? <Editors rows={overview} reload={load} /> : null}
      {admin ? <Promoters /> : null}
    </div>
  );
}
