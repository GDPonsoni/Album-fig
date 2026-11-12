import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api, post, type AssetView, type ChapterContent, type ContentPage, type ContentSticker, type OrgView, type RarityView } from '../api';
import { MosaicPage, StickerPage } from '../components/AlbumPages';
import { ImageCropper, type CropSpec } from '../components/ImageCropper';
import { Message } from '../components/ui';
import { contentToPages, gridSlots } from '../preview';

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


// Altura do cabeçalho da página, em % (o CSS usa o mesmo valor): figurinha não pode ficar por baixo dele.
const HEAD_H = { portrait: 13.5, landscape: 19 };
const sizeFor = (w: number, landscape: boolean): 'P' | 'M' | 'G' => {
  const rel = landscape ? w / 0.72 : w;
  return rel <= 24 ? 'P' : rel <= 34 ? 'M' : 'G';
};
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const round1 = (value: number) => Math.round(value * 10) / 10;

/** Botão grande de escolher imagem (o <input> fica escondido): serve para trocar fotos, fundo e mosaico. */
function PickButton({ label, disabled, onPick, primary }: { label: string; disabled?: boolean; onPick: (file: File) => void; primary?: boolean }) {
  return (
    <label className={`btn pick${primary ? ' btn-primary' : ''}${disabled ? ' pick-disabled' : ''}`}>
      {label}
      <input
        type="file"
        accept="image/png,image/webp,image/jpeg"
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onPick(file);
        }}
      />
    </label>
  );
}

type Slot = { x: number; y: number; w: number; h: number };

/** Camada por cima da página: arrastar move a figurinha, puxar o canto muda o tamanho, tocar no cabeçalho edita o texto. */
function EditLayer({ stickers, selected, head, landscape, locked, onSelect, onSlot }: {
  stickers: ContentSticker[];
  selected: string | null;
  head: boolean;
  landscape: boolean;
  locked: boolean;
  onSelect: (key: string | null) => void;
  onSlot: (key: string, slot: Slot, final: boolean) => void;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const drag = useRef<{ key: string; mode: 'move' | 'resize'; x: number; y: number; slot: Slot } | null>(null);
  const top = landscape ? HEAD_H.landscape : HEAD_H.portrait;

  const down = (event: ReactPointerEvent, sticker: ContentSticker, mode: 'move' | 'resize') => {
    event.stopPropagation();
    onSelect(sticker.key);
    if (locked) return;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    drag.current = { key: sticker.key, mode, x: event.clientX, y: event.clientY, slot: sticker.slot };
  };
  const move = (event: ReactPointerEvent) => {
    const state = drag.current;
    const box = layer.current?.getBoundingClientRect();
    if (!state || !box) return;
    const dx = ((event.clientX - state.x) / box.width) * 100;
    const dy = ((event.clientY - state.y) / box.height) * 100;
    const { slot } = state;
    if (state.mode === 'move') {
      let x = clamp(slot.x + dx, 0, 100 - slot.w);
      const y = clamp(slot.y + dy, Math.min(top, 100 - slot.h), 100 - slot.h);
      // Ímã: centraliza na página quando chega perto.
      if (Math.abs(x + slot.w / 2 - 50) < 1.8) x = 50 - slot.w / 2;
      onSlot(state.key, { ...slot, x: round1(x), y: round1(y) }, false);
    } else {
      // A figurinha mantém a proporção 3:4; a altura em % acompanha o formato da página.
      const pageRatio = box.width / box.height;
      const maxW = Math.min(100 - slot.x, ((100 - slot.y) * 3) / 4 / pageRatio);
      const w = clamp(slot.w + dx, 8, maxW);
      onSlot(state.key, { ...slot, w: round1(w), h: round1((w * pageRatio * 4) / 3) }, false);
    }
  };
  const up = (event: ReactPointerEvent) => {
    drag.current = null;
    (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
  };
  const key = (event: React.KeyboardEvent, sticker: ContentSticker) => {
    if (locked) return;
    const step = event.shiftKey ? 2 : 0.5;
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
    if (!delta) return;
    event.preventDefault();
    const { slot } = sticker;
    onSlot(sticker.key, { ...slot, x: round1(clamp(slot.x + delta[0], 0, 100 - slot.w)), y: round1(clamp(slot.y + delta[1], Math.min(top, 100 - slot.h), 100 - slot.h)) }, true);
  };

  return (
    <div className="edit-layer" ref={layer} onPointerDown={() => onSelect(null)}>
      <button type="button" className={`edit-head${head ? ' on' : ''}`} style={{ height: `${top}%` }} aria-label="Editar título e texto da página" onPointerDown={(e) => { e.stopPropagation(); onSelect('head'); }}>
      </button>
      {stickers.filter((s) => s.active).map((sticker) => (
        <div
          key={sticker.key}
          className={`edit-box${sticker.key === selected ? ' on' : ''}`}
          style={{ left: `${sticker.slot.x}%`, top: `${sticker.slot.y}%`, width: `${sticker.slot.w}%`, height: `${sticker.slot.h}%` }}
          role="button"
          tabIndex={0}
          aria-label={`Mover ${sticker.name}`}
          onPointerDown={(e) => down(e, sticker, 'move')}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
          onKeyDown={(e) => key(e, sticker)}
          onFocus={() => onSelect(sticker.key)}
        >
          {sticker.key === selected && !locked ? (
            <i
              className="edit-handle"
              aria-label="Mudar o tamanho"
              onPointerDown={(e) => down(e, sticker, 'resize')}
              onPointerMove={move}
              onPointerUp={up}
              onPointerCancel={up}
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Counter({ value, max }: { value: string; max: number }) {
  return <small className={value.length > max * 0.9 ? 'counter warn' : 'counter'}>{value.length}/{max}</small>;
}

export function EditorChapter() {
  const chapterId = Number(useParams().capitulo);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [content, setContent] = useState<ChapterContent | null>(null);
  const [assets, setAssets] = useState<AssetView[]>([]);
  const [revision, setRevision] = useState<Row | null>(null);
  const [editable, setEditable] = useState(true);
  const [tab, setTab] = useState<'pages' | 'mosaic' | 'format'>('pages');
  const [pageIndex, setPageIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [save, setSave] = useState<{ state: 'idle' | 'dirty' | 'saving' | 'saved' | 'error'; message?: string }>({ state: 'idle' });
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [cropping, setCropping] = useState<{ file: File; kind: 'sticker' | 'background' | 'mosaic'; apply: (asset: AssetView) => void } | null>(null);
  const mosaicFile = useRef<File | null>(null);
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
  const details = useMemo(
    () => (content && draft ? contentToPages(content, draft.chapter.org, assets, draft.rarities) : []),
    [content, draft, assets],
  );

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
  const current = selected && selected !== 'head' ? content.stickers.find((sticker) => sticker.key === selected) ?? null : null;
  const assetUrl = (id: number | null) => (id == null ? null : assets.find((asset) => asset.id === id)?.url ?? null);
  const detail = details.find((d) => d.kind === 'stickers' && d.page_no === page.page_no);
  const mosaicDetail = details.find((d) => d.kind === 'mosaic');

  const setPage = (changes: Partial<ContentPage>) =>
    change((c) => ({ ...c, pages: c.pages.map((p) => (p.page_no === page.page_no ? { ...p, ...changes } : p)) }));
  const setSticker = (key: string, changes: Partial<ContentSticker>) =>
    change((c) => ({ ...c, stickers: c.stickers.map((s) => (s.key === key ? { ...s, ...changes } : s)) }));
  // Mexer na posição à mão tira a página de qualquer grade pronta.
  const moveSticker = (key: string, slot: Slot) =>
    change((c) => ({
      ...c,
      pages: c.pages.map((p) => (p.page_no === page.page_no && p.layout !== 'free' ? { ...p, layout: 'free' } : p)),
      stickers: c.stickers.map((s) => (s.key === key ? { ...s, slot, size: sizeFor(slot.w, landscape) } : s)),
    }));

  const applyGrid = (layout: string) => {
    const slots = gridSlots(layout, content.layout.orientation);
    change((c) => {
      let next = 0;
      return {
        ...c,
        pages: c.pages.map((p) => (p.page_no === page.page_no ? { ...p, layout } : p)),
        stickers: c.stickers.map((s) => {
          if (s.page_no !== page.page_no || next >= slots.length) return s;
          const slot = slots[next++];
          return { ...s, slot, size: sizeFor(slot.w, landscape) };
        }),
      };
    });
  };
  const addPage = () => {
    const number = Math.max(0, ...content.pages.map((p) => p.page_no)) + 1;
    change((c) => ({ ...c, pages: [...c.pages, { page_no: number, title: '', text: '', background_asset_id: null, layout: 'free' }] }));
    setPageIndex(pages.length);
    setSelected('head');
  };
  const removePage = () => {
    change((c) => ({ ...c, pages: c.pages.filter((p) => p.page_no !== page.page_no) }));
    setPageIndex(Math.max(0, pageIndex - 1));
    setSelected(null);
  };
  const addSticker = () => {
    const key = crypto.randomUUID();
    const slots = gridSlots(page.layout, content.layout.orientation);
    const top = landscape ? HEAD_H.landscape : HEAD_H.portrait;
    const slot = slots[onPage.length] ?? slotFor('P', landscape, 5 + (onPage.length % 4) * 23, top + 4 + Math.floor(onPage.length / 4) * 24);
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
    dirty: 'Salvando em instantes…',
    saving: 'Salvando…',
    saved: `Salvo às ${save.message}`,
    error: `Não salvou: ${save.message}`,
  }[save.state];

  const mosaic = content.mosaic;
  const pickMosaic = (file: File) => {
    mosaicFile.current = file;
    setCropping({ file, kind: 'mosaic', apply: (asset) => change((c) => ({ ...c, mosaic: { cols: c.mosaic?.cols ?? 2, rows: c.mosaic?.rows ?? 3, asset_id: asset.id } })) });
  };
  const repositionMosaic = async () => {
    try {
      let file = mosaicFile.current;
      if (!file) {
        const url = assetUrl(mosaic?.asset_id ?? null);
        if (!url) return;
        const blob = await (await fetch(url)).blob();
        file = new File([blob], 'mosaico.jpg', { type: blob.type });
      }
      pickMosaic(file);
    } catch {
      setError('Não foi possível abrir a imagem. Escolha o arquivo de novo.');
    }
  };

  return (
    <div className="editor">
      {cropping ? (
        <ImageCropper file={cropping.file} spec={crop(cropping.kind, landscape)} onDone={uploadCropped} onCancel={() => setCropping(null)} />
      ) : null}
      <header className="editor-top">
        <Link to="/conta" className="editor-back">← Sair do editor</Link>
        <div>
          <p className="album-kicker">{chapter.album.title}</p>
          <h1>{chapter.org.name}</h1>
        </div>
      </header>

      {revision?.review_note ? <p className="review-note"><b>Nota da revisão:</b> {revision.review_note}</p> : null}
      {chapter.closed ? <p className="notice">Álbum encerrado: o capítulo não pode mais mudar.</p> : null}
      {frozen && !chapter.closed ? <p className="notice">O álbum já começou: você pode mudar textos, imagens e posições, mas não criar nem remover figurinhas.</p> : null}
      {locked && !chapter.closed ? <p className="notice">{revision ? STATUS[revision.status] : 'Somente leitura'}.</p> : null}
      <Message>{error}</Message>
      {note ? <Message kind="ok">{note}</Message> : null}

      <div className="segmented editor-tabs" role="tablist" aria-label="Seções do editor">
        {([['pages', 'Páginas'], ['mosaic', 'Mosaico'], ['format', 'Formato']] as const).map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{label}</button>
        ))}
      </div>

      {tab === 'pages' ? (
        <div className="editor-body">
          <div className="editor-canvas">
            <nav className="page-chips" aria-label="Páginas do capítulo">
              {pages.map((p, index) => (
                <button key={p.page_no} type="button" className={p.page_no === page.page_no ? 'active' : ''} onClick={() => { setPageIndex(index); setSelected(null); }}>{p.page_no}</button>
              ))}
              <button type="button" className="chip-add" disabled={locked || pages.length >= 30} onClick={addPage} aria-label="Nova página">+</button>
            </nav>
            <div className={`preview-stage edit-stage${landscape ? ' landscape' : ''}`}>
              {detail ? (
                <StickerPage
                  detail={detail}
                  onOpen={() => {}}
                  lastPasted={null}
                  overlay={<EditLayer stickers={onPage} selected={selected} head={selected === 'head'} landscape={landscape} locked={locked} onSelect={setSelected} onSlot={(key, slot) => moveSticker(key, slot)} />}
                />
              ) : null}
            </div>
            <p className="muted edit-tip">Arraste uma figurinha para mover. Puxe o canto dela para mudar o tamanho. Toque no topo para editar o texto da página.</p>
          </div>

          <div className="editor-inspector">
            <div className="admin-head">
              <h2>Figurinhas desta página ({onPage.length})</h2>
              <button type="button" className="btn" disabled={locked || frozen || content.stickers.length >= 200} onClick={addSticker}>+ Figurinha</button>
            </div>
            {onPage.length ? (
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
            ) : <p className="muted">Nenhuma figurinha nesta página.</p>}

            {current ? (
              <section className="panel paper inspector" aria-label={`Editar ${current.name}`}>
                <div className="inspector-photo">
                  <div className="photo-frame">{assetUrl(current.asset_id) ? <img src={assetUrl(current.asset_id)!} alt="" /> : <span>Sem imagem</span>}</div>
                  <div className="photo-actions">
                    <PickButton primary label={current.asset_id ? 'Trocar imagem' : 'Escolher imagem'} disabled={locked}
                      onPick={(file) => setCropping({ file, kind: 'sticker', apply: (asset) => setSticker(current.key, { asset_id: asset.id }) })} />
                    {current.asset_id && !locked ? <button type="button" className="link" onClick={() => setSticker(current.key, { asset_id: null })}>Tirar imagem</button> : null}
                    <small className="muted">Foto na proporção 3:4. Você escolhe o enquadramento depois.</small>
                  </div>
                </div>
                <label className="field"><span>Nome</span>
                  <input maxLength={120} disabled={locked} value={current.name} onChange={(e) => setSticker(current.key, { name: e.target.value })} />
                </label>
                <label className="field"><span>Texto do verso <Counter value={current.back_text} max={2000} /></span>
                  <textarea rows={4} maxLength={2000} disabled={locked} placeholder="O que o jogador lê quando vira a figurinha" value={current.back_text} onChange={(e) => setSticker(current.key, { back_text: e.target.value })} />
                </label>
                <div className="field"><span>Links no verso</span>
                  {current.back_links.map((link, index) => (
                    <div key={index} className="link-row">
                      <input aria-label={`Nome do link ${index + 1}`} placeholder="Nome (ex.: Instagram)" maxLength={40} disabled={locked} value={link.label}
                        onChange={(e) => setSticker(current.key, { back_links: current.back_links.map((l, i) => (i === index ? { ...l, label: e.target.value } : l)) })} />
                      <input aria-label={`Endereço do link ${index + 1}`} type="url" placeholder="https://…" maxLength={300} disabled={locked} value={link.url}
                        onChange={(e) => setSticker(current.key, { back_links: current.back_links.map((l, i) => (i === index ? { ...l, url: e.target.value } : l)) })} />
                      <button type="button" className="link danger" disabled={locked} onClick={() => setSticker(current.key, { back_links: current.back_links.filter((_, i) => i !== index) })}>Tirar</button>
                    </div>
                  ))}
                  {current.back_links.length < MAX_LINKS ? (
                    <button type="button" className="link" disabled={locked} onClick={() => setSticker(current.key, { back_links: [...current.back_links, { label: '', url: 'https://' }] })}>+ Adicionar link</button>
                  ) : null}
                </div>
                <details className="more">
                  <summary>Mais opções</summary>
                  <label className="field"><span>Categoria</span>
                    <input maxLength={40} disabled={locked} value={current.tag} onChange={(e) => setSticker(current.key, { tag: e.target.value })} />
                  </label>
                  <label className="field"><span>Dica no espaço vazio</span>
                    <input maxLength={160} disabled={locked} value={current.hint} placeholder="Ajuda o jogador a achar esta figurinha" onChange={(e) => setSticker(current.key, { hint: e.target.value })} />
                  </label>
                  <label className="field"><span>Página</span>
                    <select disabled={locked} value={current.page_no} onChange={(e) => setSticker(current.key, { page_no: Number(e.target.value) })}>
                      {pages.map((p) => <option key={p.page_no} value={p.page_no}>Página {p.page_no}</option>)}
                    </select>
                  </label>
                  <label className="field"><span>Raridade sugerida</span>
                    <select disabled={locked || frozen} value={current.suggested_rarity_id ?? ''} onChange={(e) => setSticker(current.key, { suggested_rarity_id: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Sem sugestão</option>
                      {rarities.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                    </select>
                    <small>É só uma sugestão: quem publica decide a raridade e o número.</small>
                  </label>
                  <label className="field check"><input type="checkbox" disabled={locked || frozen} checked={current.active} onChange={(e) => setSticker(current.key, { active: e.target.checked })} /> <span>Aparece na página e nos pacotes</span></label>
                  {!frozen ? <button type="button" className="link danger" disabled={locked} onClick={() => removeSticker(current.key)}>Remover figurinha</button> : null}
                </details>
              </section>
            ) : (
              <section className="panel paper inspector" aria-label="Editar página">
                <h2>Página {page.page_no}</h2>
                <label className="field"><span>Título <Counter value={page.title} max={80} /></span>
                  <input maxLength={80} disabled={locked} placeholder={chapter.org.name} value={page.title} onChange={(e) => setPage({ title: e.target.value })} />
                </label>
                <label className="field"><span>Texto <Counter value={page.text} max={600} /></span>
                  <textarea rows={3} maxLength={600} disabled={locked} placeholder="Aparece embaixo do título. Textos longos são cortados na página." value={page.text} onChange={(e) => setPage({ text: e.target.value })} />
                </label>
                <div className="field"><span>Fundo da página</span>
                  <div className="bg-row">
                    <div className={`bg-thumb${landscape ? ' landscape' : ''}`} style={assetUrl(page.background_asset_id) ? { backgroundImage: `url(${JSON.stringify(assetUrl(page.background_asset_id))})` } : undefined} />
                    <div className="photo-actions">
                      <PickButton label={page.background_asset_id ? 'Trocar fundo' : 'Escolher fundo'} disabled={locked}
                        onPick={(file) => setCropping({ file, kind: 'background', apply: (asset) => setPage({ background_asset_id: asset.id }) })} />
                      {page.background_asset_id && !locked ? <button type="button" className="link" onClick={() => setPage({ background_asset_id: null })}>Voltar ao fundo padrão</button> : null}
                    </div>
                  </div>
                </div>
                <label className="field"><span>Arrumar figurinhas em grade</span>
                  <select disabled={locked || !onPage.length} value="" onChange={(e) => e.target.value && applyGrid(e.target.value)}>
                    <option value="">{page.layout === 'free' ? 'Posição livre (arrastando)' : `Grade ${page.layout.replace('grid-', '')}`}</option>
                    {LAYOUTS.filter(([value]) => value !== 'free').map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                  <small>Alinha tudo de uma vez. Depois você ainda pode arrastar cada uma.</small>
                </label>
                {pages.length > 1 && !onPage.length ? <button type="button" className="link danger" disabled={locked} onClick={removePage}>Remover esta página</button> : null}
              </section>
            )}
          </div>
        </div>
      ) : null}

      {tab === 'mosaic' ? (
        <div className="editor-body">
          <div className="editor-canvas">
            <div className={`preview-stage edit-stage`}>
              {mosaicDetail ? <MosaicPage detail={mosaicDetail} onOpen={() => {}} lastPasted={null} /> : <p className="empty-mosaic">Nenhuma imagem de mosaico ainda.</p>}
            </div>
          </div>
          <div className="editor-inspector">
            <section className="panel paper inspector">
              <h2>Mosaico</h2>
              <p className="muted">Uma imagem grande cortada em peças. Cada peça vira uma figurinha que os jogadores juntam para formar a imagem.</p>
              <div className="photo-actions row">
                <PickButton primary label={mosaic?.asset_id ? 'Trocar imagem' : 'Escolher imagem'} disabled={locked || (frozen && mosaic?.asset_id == null)} onPick={pickMosaic} />
                {mosaic?.asset_id ? <button type="button" className="btn" disabled={locked} onClick={repositionMosaic}>Reposicionar</button> : null}
              </div>
              {mosaic?.asset_id && !frozen && !locked ? <button type="button" className="link danger" onClick={() => change((c) => ({ ...c, mosaic: null }))}>Remover mosaico</button> : null}
              {mosaic ? (
                <div className="steppers">
                  {([['cols', 'Colunas'], ['rows', 'Linhas']] as const).map(([axis, label]) => (
                    <div key={axis} className="stepper">
                      <span>{label}</span>
                      <button type="button" aria-label={`Menos ${label.toLowerCase()}`} disabled={locked || frozen || mosaic[axis] <= 1} onClick={() => change((c) => ({ ...c, mosaic: { ...c.mosaic!, [axis]: c.mosaic![axis] - 1 } }))}>−</button>
                      <b>{mosaic[axis]}</b>
                      <button type="button" aria-label={`Mais ${label.toLowerCase()}`} disabled={locked || frozen || mosaic[axis] >= 10} onClick={() => change((c) => ({ ...c, mosaic: { ...c.mosaic!, [axis]: c.mosaic![axis] + 1 } }))}>+</button>
                    </div>
                  ))}
                  <p className="muted">{mosaic.cols * mosaic.rows} peças no total.{frozen ? ' O álbum já começou: a grade não pode mudar.' : ''}</p>
                </div>
              ) : null}
            </section>
          </div>
        </div>
      ) : null}

      {tab === 'format' ? (
        <section className="panel paper inspector">
          <h2>Formato das páginas</h2>
          <div className="segmented" role="group" aria-label="Orientação das páginas">
            {(['portrait', 'landscape'] as const).map((value) => (
              <button key={value} type="button" disabled={locked} className={content.layout.orientation === value ? 'active' : ''} onClick={() => change((c) => ({ ...c, layout: { orientation: value } }))}>
                {value === 'portrait' ? 'Em pé' : 'Deitado'}
              </button>
            ))}
          </div>
          <p className="muted">Vale para todas as páginas do capítulo. Ao mudar, confira a posição das figurinhas.</p>
        </section>
      ) : null}

      <footer className="editor-bar">
        <span className={`save-state save-${save.state}`} role="status">{saveLabel}</span>
        {save.state === 'error' ? <button className="btn" onClick={persist}>Tentar de novo</button> : null}
        <button className="btn btn-primary" disabled={locked || save.state === 'saving' || (!revision && save.state === 'idle')} onClick={submit}>Enviar para revisão</button>
      </footer>
    </div>
  );
}
