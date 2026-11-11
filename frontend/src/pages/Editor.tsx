import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api, post, type AssetView, type ChapterContent, type ContentPage, type ContentSticker, type OrgView, type RarityView } from '../api';
import { ContentPreview } from '../components/ContentPreview';
import { ImageCropper, type CropSpec } from '../components/ImageCropper';
import { Message } from '../components/ui';
import { gridSlots } from '../preview';

type Row = Record<string, any>;
interface Draft {
  chapter: { id: number; org: OrgView; album: Row; frozen: boolean; closed: boolean };
  revision: Row | null;
  content: ChapterContent;
  published: ChapterContent;
  editable: boolean;
  assets: AssetView[];
  rarities: RarityView[];
}

const STATUS: Record<string, string> = {
  draft: 'Rascunho', submitted: 'Enviado para revisão', approved: 'Aprovado, aguardando publicação', rejected: 'Rejeitado: ajuste e envie de novo',
};
const LAYOUTS: [string, string][] = [
  ['free', 'Livre (posições por figurinha)'], ['grid-2x2', 'Grade 2 × 2'], ['grid-2x3', 'Grade 2 × 3'],
  ['grid-3x3', 'Grade 3 × 3'], ['grid-3x4', 'Grade 3 × 4'], ['grid-4x4', 'Grade 4 × 4'],
];
// Largura do espaço por tamanho, em % da página; a altura sai da proporção 3:4 da figurinha.
const SIZE_W = { P: 21, M: 28, G: 42 };
const MAX_LINKS = 5;

function crop(kind: 'sticker' | 'background' | 'mosaic', landscape: boolean): CropSpec {
  if (kind === 'sticker') return { width: 480, height: 640, type: 'image/png', label: 'a figurinha' };
  if (kind === 'mosaic') return { width: 800, height: 1200, type: 'image/jpeg', label: 'o mosaico' };
  return landscape
    ? { width: 1380, height: 1000, type: 'image/jpeg', label: 'o fundo da página' }
    : { width: 1000, height: 1380, type: 'image/jpeg', label: 'o fundo da página' };
}

const slotFor = (size: 'P' | 'M' | 'G', landscape: boolean, x = 5, y = 20) => {
  const ratio = landscape ? 1.38 : 1 / 1.38;
  const w = landscape ? SIZE_W[size] * 0.72 : SIZE_W[size];
  const h = Math.round(((w * ratio * 4) / 3) * 10) / 10;
  return { x: Math.min(x, 100 - w), y: Math.min(y, 100 - h), w, h };
};

/** Lista: os capítulos que a pessoa edita. Com um só, vai direto para ele. */
export function EditorHome() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api<Row[]>('/editor/chapters').then(setRows).catch((e) => setError(e.message));
  }, []);
  if (rows?.length === 1) return <Navigate to={`/editor/${rows[0].id}`} replace />;
  return (
    <div className="screen">
      <h1 className="screen-title">Editor</h1>
      <Message>{error}</Message>
      {rows && !rows.length ? (
        <section className="panel paper empty-album">
          <h2>Você não edita nenhum capítulo</h2>
          <p className="muted">O acesso de editor é dado por quem administra o álbum.</p>
          <Link className="btn" to="/">Voltar ao álbum</Link>
        </section>
      ) : null}
      <ul className="album-list">
        {rows?.map((row) => (
          <li key={row.id} className="panel paper album-card">
            <h3>{row.org.name}</h3>
            <p className="muted">Álbum {row.album.title} · {row.revision ? STATUS[row.revision.status] ?? row.revision.status : 'sem rascunho'}</p>
            <Link className="btn btn-primary" to={`/editor/${row.id}`}>Editar capítulo</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ImagePick({ label, url, disabled, onPick, onClear }: { label: string; url: string | null; disabled: boolean; onPick: (file: File) => void; onClear?: () => void }) {
  return (
    <div className="image-field">
      {url ? <img src={url} alt="" /> : null}
      <input
        type="file"
        aria-label={label}
        accept="image/png,image/webp,image/jpeg"
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onPick(file);
        }}
      />
      {url && onClear && !disabled ? <button type="button" className="link" onClick={onClear}>Tirar imagem</button> : null}
    </div>
  );
}

export function EditorChapter() {
  const chapterId = Number(useParams().capitulo);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [content, setContent] = useState<ChapterContent | null>(null);
  const [assets, setAssets] = useState<AssetView[]>([]);
  const [revision, setRevision] = useState<Row | null>(null);
  const [editable, setEditable] = useState(true);
  const [pageIndex, setPageIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [save, setSave] = useState<{ state: 'idle' | 'dirty' | 'saving' | 'saved' | 'error'; message?: string }>({ state: 'idle' });
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [cropping, setCropping] = useState<{ file: File; kind: 'sticker' | 'background' | 'mosaic'; apply: (asset: AssetView) => void } | null>(null);
  const base = `/editor/chapters/${chapterId}`;

  useEffect(() => {
    api<Draft>(`${base}/draft`)
      .then((data) => {
        setDraft(data);
        setContent(data.content);
        setAssets(data.assets);
        setRevision(data.revision);
        setEditable(data.editable && !data.chapter.closed);
      })
      .catch((e) => setError(e.message));
  }, [base]);

  // ---- salvar: automático, pouco depois da última mudança
  const latest = useRef<ChapterContent | null>(null);
  latest.current = content;
  const saving = useRef<Promise<boolean> | null>(null);
  const persist = useCallback(async (): Promise<boolean> => {
    if (saving.current) await saving.current;
    const body = latest.current;
    if (!body) return false;
    setSave({ state: 'saving' });
    // Linha de link ainda em branco (só o "https://" inicial) não impede de salvar o resto: fica de fora.
    const clean: ChapterContent = {
      ...body,
      stickers: body.stickers.map((sticker) => ({
        ...sticker,
        back_links: sticker.back_links.filter((link) => link.label.trim() !== '' || !['', 'https://'].includes(link.url.trim())),
      })),
    };
    const request = api<{ revision: Row }>(`${base}/draft`, { method: 'PUT', body: { content: clean } }).then(
      (result) => {
        setRevision(result.revision);
        // Se o conteúdo mudou enquanto salvava, continua "por salvar".
        setSave(latest.current === body ? { state: 'saved', message: new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) } : { state: 'dirty' });
        return true;
      },
      (err: Error) => {
        // Se a pessoa já corrigiu enquanto salvava, o que falhou é passado: tenta o conteúdo novo.
        setSave(latest.current === body ? { state: 'error', message: err.message } : { state: 'dirty' });
        return false;
      },
    );
    saving.current = request;
    const ok = await request;
    saving.current = null;
    return ok;
  }, [base]);

  useEffect(() => {
    if (save.state !== 'dirty') return;
    const timer = window.setTimeout(persist, 1500);
    return () => window.clearTimeout(timer);
  }, [content, persist, save.state]);

  // Aviso ao sair com alterações que ainda não chegaram ao servidor.
  const unsaved = save.state === 'dirty' || save.state === 'saving' || save.state === 'error';
  useEffect(() => {
    if (!unsaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [unsaved]);

  const change = useCallback((update: (current: ChapterContent) => ChapterContent) => {
    setContent((current) => (current ? update(current) : current));
    setSave({ state: 'dirty' });
    setNote('');
  }, []);

  const pages = useMemo(() => (content ? [...content.pages].sort((a, b) => a.page_no - b.page_no) : []), [content]);
  if (error && !draft) {
    return <div className="screen"><h1 className="screen-title">Editor</h1><Message>{error}</Message><Link className="btn" to="/">Voltar ao álbum</Link></div>;
  }
  if (!draft || !content) return <div className="screen"><div className="album-skeleton" /></div>;

  const { chapter, rarities } = draft;
  const frozen = chapter.frozen; // álbum em andamento: só o cosmético muda
  const locked = !editable;
  const landscape = content.layout.orientation === 'landscape';
  const page = pages[Math.min(pageIndex, pages.length - 1)];
  const onPage = content.stickers.filter((sticker) => sticker.page_no === page.page_no);
  const current = content.stickers.find((sticker) => sticker.key === selected) ?? null;
  const assetUrl = (id: number | null) => (id == null ? null : assets.find((asset) => asset.id === id)?.url ?? null);

  const setPage = (changes: Partial<ContentPage>) =>
    change((c) => ({ ...c, pages: c.pages.map((p) => (p.page_no === page.page_no ? { ...p, ...changes } : p)) }));
  const setSticker = (key: string, changes: Partial<ContentSticker>) =>
    change((c) => ({ ...c, stickers: c.stickers.map((s) => (s.key === key ? { ...s, ...changes } : s)) }));

  const applyGrid = (layout: string) => {
    const slots = gridSlots(layout, content.layout.orientation);
    change((c) => {
      let next = 0;
      return {
        ...c,
        pages: c.pages.map((p) => (p.page_no === page.page_no ? { ...p, layout } : p)),
        stickers: c.stickers.map((s) => (s.page_no === page.page_no && next < slots.length ? { ...s, slot: slots[next++] } : s)),
      };
    });
  };
  const addPage = () => {
    const number = Math.max(0, ...content.pages.map((p) => p.page_no)) + 1;
    change((c) => ({ ...c, pages: [...c.pages, { page_no: number, title: '', text: '', background_asset_id: null, layout: 'free' }] }));
    setPageIndex(pages.length);
  };
  const removePage = () => {
    change((c) => ({ ...c, pages: c.pages.filter((p) => p.page_no !== page.page_no) }));
    setPageIndex(Math.max(0, pageIndex - 1));
  };
  const addSticker = () => {
    const key = crypto.randomUUID();
    const slots = gridSlots(page.layout, content.layout.orientation);
    const slot = slots[onPage.length] ?? slotFor('P', landscape, 5 + (onPage.length % 4) * 23, 20 + Math.floor(onPage.length / 4) * 24);
    change((c) => ({
      ...c,
      stickers: [...c.stickers, {
        key, name: 'Nova figurinha', asset_id: null, size: 'P', tag: 'membro', hint: '', back_text: '', back_links: [],
        page_no: page.page_no, slot, suggested_rarity_id: null, active: true,
      }],
    }));
    setSelected(key);
  };
  const removeSticker = (key: string) => {
    change((c) => ({ ...c, stickers: c.stickers.filter((s) => s.key !== key) }));
    setSelected(null);
  };

  const uploadCropped = async (blob: Blob) => {
    const job = cropping;
    setCropping(null);
    if (!job) return;
    setError('');
    const form = new FormData();
    form.append('file', new File([blob], blob.type === 'image/png' ? 'recorte.png' : 'recorte.jpg', { type: blob.type }));
    form.append('kind', job.kind);
    try {
      const asset = await api<AssetView>(`${base}/assets`, { form });
      setAssets((all) => [...all, asset]);
      job.apply(asset);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const submit = async () => {
    setError('');
    setNote('');
    if (save.state !== 'saved' && save.state !== 'idle' && !(await persist())) return;
    if (save.state === 'idle' && !revision && !(await persist())) return;
    try {
      const result = await post<{ revision: Row }>(`${base}/submit`);
      setRevision(result.revision);
      setEditable(false);
      setSave({ state: 'idle' });
      setNote('Enviado. Quem administra o álbum vai revisar; até lá o rascunho fica travado.');
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const saveLabel = {
    idle: revision ? 'Tudo salvo' : 'Nada alterado ainda',
    dirty: 'Alterações por salvar…',
    saving: 'Salvando…',
    saved: `Salvo às ${save.message}`,
    error: `Não salvou: ${save.message}`,
  }[save.state];

  return (
    <div className="editor">
      {cropping ? (
        <ImageCropper file={cropping.file} spec={crop(cropping.kind, landscape)} onDone={uploadCropped} onCancel={() => setCropping(null)} />
      ) : null}
      <header className="editor-top">
        <div>
          <p className="album-kicker">Editor · {chapter.album.title}</p>
          <h1>{chapter.org.name}</h1>
        </div>
        <Link to="/">Voltar ao álbum</Link>
      </header>

      <section className="panel paper editor-status" aria-label="Estado da revisão">
        <p>
          <b>{revision ? STATUS[revision.status] ?? revision.status : 'Sem rascunho: você está vendo o que está publicado'}</b>
          <small className={`save-state save-${save.state}`} role="status"> · {saveLabel}</small>
        </p>
        {revision?.review_note ? <p className="review-note"><b>Nota da revisão:</b> {revision.review_note}</p> : null}
        {chapter.closed ? <p className="muted">Álbum encerrado: o capítulo não pode mais mudar.</p> : null}
        {frozen && !chapter.closed ? <p className="muted">Álbum em andamento: dá para mudar textos, imagens e posições. Não dá para criar, remover ou desativar figurinhas, nem refazer a grade do mosaico.</p> : null}
        <Message>{error}</Message>
        {note ? <Message kind="ok">{note}</Message> : null}
        <div className="admin-actions">
          <button className="btn btn-primary" disabled={locked || save.state === 'saving' || (!revision && save.state === 'idle')} onClick={submit}>Enviar para revisão</button>
          {save.state === 'error' ? <button className="btn" onClick={persist}>Tentar salvar de novo</button> : null}
        </div>
      </section>

      <div className="editor-body">
        <div className="editor-forms">
          <section className="panel paper">
            <h2>Formato do capítulo</h2>
            <div className="segmented" role="group" aria-label="Orientação das páginas">
              {(['portrait', 'landscape'] as const).map((value) => (
                <button key={value} type="button" disabled={locked} className={content.layout.orientation === value ? 'active' : ''} onClick={() => change((c) => ({ ...c, layout: { orientation: value } }))}>
                  {value === 'portrait' ? 'Em pé' : 'Deitado'}
                </button>
              ))}
            </div>
          </section>

          <section className="panel paper">
            <div className="admin-head">
              <h2>Páginas</h2>
              <button type="button" className="btn" disabled={locked || pages.length >= 30} onClick={addPage}>Nova página</button>
            </div>
            <nav className="admin-tabs" aria-label="Páginas do capítulo">
              {pages.map((p, index) => (
                <button key={p.page_no} type="button" className={p.page_no === page.page_no ? 'active' : ''} onClick={() => { setPageIndex(index); setSelected(null); }}>Página {p.page_no}</button>
              ))}
            </nav>
            <label className="field"><span>Título da página</span>
              <input maxLength={80} disabled={locked} placeholder={chapter.org.name} value={page.title} onChange={(e) => setPage({ title: e.target.value })} />
            </label>
            <label className="field"><span>Texto</span>
              <textarea rows={2} maxLength={600} disabled={locked} value={page.text} onChange={(e) => setPage({ text: e.target.value })} />
            </label>
            <label className="field"><span>Layout</span>
              <select disabled={locked} value={page.layout} onChange={(e) => (e.target.value === 'free' ? setPage({ layout: 'free' }) : applyGrid(e.target.value))}>
                {LAYOUTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <small>Escolher uma grade reposiciona as figurinhas desta página.</small>
            </label>
            <div className="field"><span>Fundo da página</span>
              <ImagePick
                label="Fundo da página"
                url={assetUrl(page.background_asset_id)}
                disabled={locked}
                onPick={(file) => setCropping({ file, kind: 'background', apply: (asset) => setPage({ background_asset_id: asset.id }) })}
                onClear={() => setPage({ background_asset_id: null })}
              />
            </div>
            {pages.length > 1 && !onPage.length ? <button type="button" className="link danger" disabled={locked} onClick={removePage}>Remover esta página</button> : null}
          </section>

          <section className="panel paper">
            <div className="admin-head">
              <h2>Figurinhas da página {page.page_no} ({onPage.length})</h2>
              <button type="button" className="btn" disabled={locked || frozen || content.stickers.length >= 200} onClick={addSticker}>Nova figurinha</button>
            </div>
            <ul className="editor-stickers">
              {onPage.map((sticker) => (
                <li key={sticker.key}>
                  <button type="button" className={`editor-sticker${sticker.key === selected ? ' active' : ''}${sticker.active ? '' : ' inactive'}`} onClick={() => setSelected(sticker.key === selected ? null : sticker.key)}>
                    {assetUrl(sticker.asset_id) ? <img src={assetUrl(sticker.asset_id)!} alt="" /> : <i />}
                    <span>{sticker.name}</span>
                  </button>
                </li>
              ))}
            </ul>
            {onPage.length ? null : <p className="muted">Nenhuma figurinha nesta página.</p>}
            {current ? (
              <div className="editor-form" aria-label={`Editar ${current.name}`}>
                <label className="field"><span>Nome</span>
                  <input maxLength={120} disabled={locked} value={current.name} onChange={(e) => setSticker(current.key, { name: e.target.value })} />
                </label>
                <div className="field"><span>Imagem (proporção 3:4)</span>
                  <ImagePick
                    label={`Imagem de ${current.name}`}
                    url={assetUrl(current.asset_id)}
                    disabled={locked}
                    onPick={(file) => setCropping({ file, kind: 'sticker', apply: (asset) => setSticker(current.key, { asset_id: asset.id }) })}
                    onClear={() => setSticker(current.key, { asset_id: null })}
                  />
                </div>
                <label className="field"><span>Categoria</span>
                  <input maxLength={40} disabled={locked} value={current.tag} onChange={(e) => setSticker(current.key, { tag: e.target.value })} />
                </label>
                <label className="field"><span>Dica no espaço vazio</span>
                  <input maxLength={160} disabled={locked} value={current.hint} onChange={(e) => setSticker(current.key, { hint: e.target.value })} />
                </label>
                <label className="field"><span>Texto do verso</span>
                  <textarea rows={3} maxLength={2000} disabled={locked} value={current.back_text} onChange={(e) => setSticker(current.key, { back_text: e.target.value })} />
                </label>
                <div className="field"><span>Links do verso (só https)</span>
                  {current.back_links.map((link, index) => (
                    <div key={index} className="link-row">
                      <input aria-label={`Nome do link ${index + 1}`} placeholder="Instagram" maxLength={40} disabled={locked} value={link.label}
                        onChange={(e) => setSticker(current.key, { back_links: current.back_links.map((l, i) => (i === index ? { ...l, label: e.target.value } : l)) })} />
                      <input aria-label={`Endereço do link ${index + 1}`} type="url" placeholder="https://…" maxLength={300} disabled={locked} value={link.url}
                        onChange={(e) => setSticker(current.key, { back_links: current.back_links.map((l, i) => (i === index ? { ...l, url: e.target.value } : l)) })} />
                      <button type="button" className="link danger" disabled={locked} onClick={() => setSticker(current.key, { back_links: current.back_links.filter((_, i) => i !== index) })}>Tirar</button>
                    </div>
                  ))}
                  {current.back_links.length < MAX_LINKS ? (
                    <button type="button" className="link" disabled={locked} onClick={() => setSticker(current.key, { back_links: [...current.back_links, { label: '', url: 'https://' }] })}>Adicionar link</button>
                  ) : null}
                </div>
                <label className="field"><span>Página</span>
                  <select disabled={locked} value={current.page_no} onChange={(e) => setSticker(current.key, { page_no: Number(e.target.value) })}>
                    {pages.map((p) => <option key={p.page_no} value={p.page_no}>Página {p.page_no}</option>)}
                  </select>
                </label>
                <label className="field"><span>Tamanho</span>
                  <select disabled={locked} value={current.size} onChange={(e) => {
                    const size = e.target.value as 'P' | 'M' | 'G';
                    setSticker(current.key, { size, slot: slotFor(size, landscape, current.slot.x, current.slot.y) });
                  }}>
                    <option value="P">Pequena</option><option value="M">Média</option><option value="G">Grande</option>
                  </select>
                </label>
                <div className="field"><span>Posição na página (%)</span>
                  <div className="slot-row">
                    {(['x', 'y', 'w', 'h'] as const).map((axis) => (
                      <label key={axis} className="inline-field">{{ x: 'Esquerda', y: 'Topo', w: 'Largura', h: 'Altura' }[axis]}
                        <input type="number" min={axis === 'w' || axis === 'h' ? 1 : 0} max={100} step="0.5" disabled={locked} value={current.slot[axis]}
                          onChange={(e) => setSticker(current.key, { slot: { ...current.slot, [axis]: Number(e.target.value) } })} />
                      </label>
                    ))}
                  </div>
                </div>
                <label className="field"><span>Raridade sugerida</span>
                  <select disabled={locked || frozen} value={current.suggested_rarity_id ?? ''} onChange={(e) => setSticker(current.key, { suggested_rarity_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">Sem sugestão</option>
                    {rarities.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                  <small>É só uma sugestão: quem publica decide a raridade e o número.</small>
                </label>
                <label className="field check"><input type="checkbox" disabled={locked || frozen} checked={current.active} onChange={(e) => setSticker(current.key, { active: e.target.checked })} /> <span>Ativa (entra nos pacotes)</span></label>
                {!frozen ? <button type="button" className="link danger" disabled={locked} onClick={() => removeSticker(current.key)}>Remover figurinha</button> : null}
              </div>
            ) : null}
          </section>

          <section className="panel paper">
            <h2>Mosaico</h2>
            <p className="muted">Uma imagem cortada em peças, que os jogadores juntam. Sem imagem, o capítulo não tem mosaico.</p>
            <ImagePick
              label="Imagem do mosaico"
              url={assetUrl(content.mosaic?.asset_id ?? null)}
              disabled={locked || (frozen && content.mosaic?.asset_id == null)}
              onPick={(file) => setCropping({ file, kind: 'mosaic', apply: (asset) => change((c) => ({ ...c, mosaic: { cols: c.mosaic?.cols ?? 2, rows: c.mosaic?.rows ?? 3, asset_id: asset.id } })) })}
              onClear={frozen ? undefined : () => change((c) => ({ ...c, mosaic: null }))}
            />
            {content.mosaic ? (
              <div className="slot-row">
                <label className="inline-field">Colunas
                  <input type="number" min={1} max={10} disabled={locked || frozen} value={content.mosaic.cols} onChange={(e) => change((c) => ({ ...c, mosaic: { ...c.mosaic!, cols: Number(e.target.value) || 1 } }))} />
                </label>
                <label className="inline-field">Linhas
                  <input type="number" min={1} max={10} disabled={locked || frozen} value={content.mosaic.rows} onChange={(e) => change((c) => ({ ...c, mosaic: { ...c.mosaic!, rows: Number(e.target.value) || 1 } }))} />
                </label>
              </div>
            ) : null}
          </section>
        </div>

        <aside className="editor-preview">
          <ContentPreview label="Prévia ao vivo" content={content} org={chapter.org} assets={assets} rarities={rarities} page={Math.min(pageIndex, pages.length - 1)} onPage={(index) => index < pages.length && setPageIndex(index)} />
          <p className="muted">É assim que o jogador vê a página com as figurinhas coladas. Sem imagem, aparece o espaço vazio com a dica.</p>
        </aside>
      </div>
    </div>
  );
}
