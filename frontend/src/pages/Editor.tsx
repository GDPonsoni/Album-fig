import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { api, post, type AssetView, type ChapterContent, type CropState, type ContentPage, type ContentSticker, type MosaicPiece, type OrgView, type RarityView } from '../api';
import { MosaicPage, StickerPage } from '../components/AlbumPages';
import { ImageCropper, type CropSpec } from '../components/ImageCropper';
import { Message, StickerArt } from '../components/ui';
import { StickerSheet } from '../components/StickerSheet';
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

function crop(kind: 'sticker' | 'background' | 'mosaic' | 'logo', landscape: boolean, grid = { cols: 2, rows: 3 }): CropSpec {
  if (kind === 'logo') return { width: 480, height: 480, type: 'image/png', label: 'o logo' };
  if (kind === 'sticker') return { width: 480, height: 640, type: 'image/png', label: 'a figurinha' };
  // Cada peça é uma figurinha 3:4: a imagem inteira tem a proporção (3 x colunas) : (4 x linhas).
  if (kind === 'mosaic') return { width: 300 * grid.cols, height: 400 * grid.rows, type: 'image/jpeg', label: 'o mosaico' };
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

type Slot = { x: number; y: number; w: number; h: number; rot?: number };

/** Camada por cima da página: arrastar move a figurinha, puxar o canto muda o tamanho, tocar no cabeçalho edita o texto. */
function EditLayer({ stickers, selected, head, landscape, locked, arrange, onSelect, onSlot }: {
  stickers: ContentSticker[];
  selected: string | null;
  head: boolean;
  landscape: boolean;
  locked: boolean;
  arrange: boolean; // só com o modo "mover" ligado as figurinhas arrastam; fora dele o toque só seleciona
  onSelect: (key: string | null) => void;
  onSlot: (key: string, slot: Slot, final: boolean) => void;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const drag = useRef<{ key: string; mode: 'move' | 'resize'; x: number; y: number; slot: Slot } | null>(null);
  const top = landscape ? HEAD_H.landscape : HEAD_H.portrait;

  const down = (event: ReactPointerEvent, sticker: ContentSticker, mode: 'move' | 'resize') => {
    event.stopPropagation();
    onSelect(sticker.key);
    if (locked || !arrange) return;
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
    if (locked || !arrange) return;
    const step = event.shiftKey ? 2 : 0.5;
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
    if (!delta) return;
    event.preventDefault();
    const { slot } = sticker;
    onSlot(sticker.key, { ...slot, x: round1(clamp(slot.x + delta[0], 0, 100 - slot.w)), y: round1(clamp(slot.y + delta[1], Math.min(top, 100 - slot.h), 100 - slot.h)) }, true);
  };

  return (
    <div className={`edit-layer${arrange && !locked ? ' arrange' : ''}`} ref={layer} onPointerDown={() => onSelect(null)}>
      <button type="button" className={`edit-head${head ? ' on' : ''}`} style={{ height: `${top}%` }} aria-label="Editar título e texto da página" onPointerDown={(e) => { e.stopPropagation(); onSelect('head'); }}>
      </button>
      {stickers.filter((s) => s.active).map((sticker) => (
        <div
          key={sticker.key}
          className={`edit-box${sticker.key === selected ? ' on' : ''}`}
          style={{ left: `${sticker.slot.x}%`, top: `${sticker.slot.y}%`, width: `${sticker.slot.w}%`, height: `${sticker.slot.h}%`, ...(sticker.slot.rot ? { transform: `rotate(${sticker.slot.rot}deg)` } : null) }}
          role="button"
          tabIndex={0}
          aria-label={`${arrange ? 'Mover' : 'Selecionar'} ${sticker.name}`}
          onPointerDown={(e) => down(e, sticker, 'move')}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
          onKeyDown={(e) => key(e, sticker)}
          onFocus={() => onSelect(sticker.key)}
        >
          {sticker.key === selected && !locked && arrange ? (
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
  const [inspect, setInspect] = useState<string | null>(null);
  const [arrange, setArrange] = useState(false);
  const [mosaicLines, setMosaicLines] = useState(true);
  const [piece, setPieceIndex] = useState<number | null>(null);
  const [pieceSheet, setPieceSheet] = useState(false);
  const [pieceImage, setPieceImage] = useState<string | null>(null);
  const [depth, setDepth] = useState({ past: 0, future: 0 });
  const history = useRef<{ past: ChapterContent[]; future: ChapterContent[]; at: number }>({ past: [], future: [], at: 0 });
  const [tab, setTab] = useState<'pages' | 'general'>('pages');
  const [mosaicPage, setMosaicPage] = useState(false);
  const [pageIndex, setPageIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [save, setSave] = useState<{ state: 'idle' | 'dirty' | 'saving' | 'saved' | 'error'; message?: string }>({ state: 'idle' });
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [cropping, setCropping] = useState<{ file: File; kind: 'sticker' | 'background' | 'mosaic' | 'logo'; initial?: CropState | null; newSource?: boolean; apply: (asset: AssetView, state: CropState, sourceId?: number) => void } | null>(null);
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
    const blank = (link: { label: string; url: string }) => link.label.trim() === '' && ['', 'https://'].includes(link.url.trim());
    const clean: ChapterContent = {
      ...body,
      stickers: body.stickers.map((sticker) => ({ ...sticker, back_links: sticker.back_links.filter((link) => !blank(link)) })),
      mosaic: body.mosaic
        ? { ...body.mosaic, pieces: body.mosaic.pieces?.map((item) => ({ ...item, back_links: item.back_links.filter((link) => !blank(link)) })) }
        : body.mosaic,
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
    const before = latest.current;
    if (before) {
      // Mudanças em sequência rápida (digitar, arrastar) viram um passo só do "Desfazer".
      const h = history.current;
      if (Date.now() - h.at > 700 || !h.past.length) {
        h.past = [...h.past.slice(-79), before];
        setDepth({ past: h.past.length, future: 0 });
      }
      h.at = Date.now();
      h.future = [];
    }
    setContent((current) => (current ? update(current) : current));
    setSave({ state: 'dirty' });
    setNote('');
  }, []);

  const travel = useCallback((direction: 'past' | 'future') => {
    const h = history.current;
    const target = h[direction].at(-1);
    const now = latest.current;
    if (!target || !now) return;
    h[direction] = h[direction].slice(0, -1);
    const other = direction === 'past' ? 'future' : 'past';
    h[other] = [...h[other], now];
    h.at = 0;
    setDepth({ past: h.past.length, future: h.future.length });
    setContent(target);
    setSave({ state: 'dirty' });
    setNote('');
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return; // os campos têm o desfazer deles
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'z') return;
      event.preventDefault();
      travel(event.shiftKey ? 'future' : 'past');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [travel]);

  const pages = useMemo(() => (content ? [...content.pages].sort((a, b) => a.page_no - b.page_no) : []), [content]);
  const details = useMemo(
    () => (content && draft ? contentToPages(content, draft.chapter.org, assets, draft.rarities) : []),
    [content, draft, assets],
  );

  // Imagem da peça para a prévia: fatia da imagem do mosaico (o servidor faz o mesmo corte).
  const mosaicNow = content?.mosaic ?? null;
  const mosaicUrl = mosaicNow?.asset_id == null ? null : assets.find((asset) => asset.id === mosaicNow.asset_id)?.url ?? null;
  const mosaicCols = mosaicNow?.cols ?? 0;
  const mosaicRows = mosaicNow?.rows ?? 0;
  useEffect(() => {
    setPieceImage(null);
    if (piece === null || !mosaicUrl || !mosaicCols) return;
    let alive = true;
    const element = new Image();
    element.onload = () => {
      const w = element.naturalWidth / mosaicCols;
      const h = element.naturalHeight / mosaicRows;
      const out = document.createElement('canvas');
      out.width = 300;
      out.height = 400;
      out.getContext('2d')?.drawImage(element, (piece % mosaicCols) * w, Math.floor(piece / mosaicCols) * h, w, h, 0, 0, 300, 400);
      if (alive) setPieceImage(out.toDataURL('image/jpeg', 0.85));
    };
    element.src = mosaicUrl;
    return () => {
      alive = false;
    };
  }, [piece, mosaicCols, mosaicRows, mosaicUrl]);

  if (error && !draft) {
    return <div className="screen"><h1 className="screen-title">Editor</h1><Message>{error}</Message><Link className="btn" to="/">Voltar ao álbum</Link></div>;
  }
  if (!draft || !content) return <div className="screen"><div className="album-skeleton" /></div>;

  const { chapter, rarities } = draft;
  const frozen = chapter.frozen; // álbum em andamento: só o cosmético muda e figurinha nova entra
  const published = new Set(draft.published.stickers.map((sticker) => sticker.key));
  const fixed = (key: string) => frozen && published.has(key); // já em produção: raridade, ativa e remoção congeladas
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
  // Girar não muda posição nem tamanho: a grade da página continua valendo.
  const setRot = (sticker: ContentSticker, rot: number) => setSticker(sticker.key, { slot: { ...sticker.slot, rot: Math.max(-180, Math.min(180, Math.round(rot))) } });
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
    if (!window.confirm(`Remover a página ${page.page_no}?`)) return;
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
    if (!window.confirm('Remover esta figurinha? Dá para desfazer logo em seguida.')) return;
    change((c) => ({ ...c, stickers: c.stickers.filter((s) => s.key !== key) }));
    setSelected(null);
  };

  const differs = JSON.stringify(content) !== JSON.stringify(draft.published);
  const restoreAll = () => {
    if (!window.confirm('Voltar tudo ao que está publicado hoje? As alterações deste rascunho serão descartadas (você ainda pode usar Desfazer).')) return;
    change(() => JSON.parse(JSON.stringify(draft.published)));
    setSelected(null);
    setPageIndex(0);
  };
  const restoreSticker = (key: string) => {
    const original = draft.published.stickers.find((s) => s.key === key);
    if (!original || !window.confirm('Voltar esta figurinha ao que está publicado?')) return;
    change((c) => ({ ...c, stickers: c.stickers.map((s) => (s.key === key ? JSON.parse(JSON.stringify(original)) : s)) }));
  };
  const cardOf = (key: string) => {
    const index = content.stickers.findIndex((s) => s.key === key);
    return details.flatMap((p) => p.slots).find((slot) => slot.card && slot.card.id === -(index + 1))?.card;
  };

  const uploadCropped = async (blob: Blob, state: CropState) => {
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
      let sourceId: number | undefined;
      if (job.kind === 'mosaic' && job.newSource) {
        // A original fica guardada (privada) para o mosaico poder ser reenquadrado depois sem reenviar.
        const original = new FormData();
        original.append('file', job.file);
        original.append('kind', 'mosaic_source');
        const source = await api<AssetView>(`${base}/assets`, { form: original });
        setAssets((all) => [...all, source]);
        sourceId = source.id;
      }
      job.apply(asset, state, sourceId);
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

  const withdraw = async () => {
    setError('');
    setNote('');
    try {
      const result = await post<{ revision: Row }>(`${base}/withdraw`);
      setRevision(result.revision);
      setEditable(!chapter.closed);
      setNote('Envio retirado. O rascunho voltou a ficar editável.');
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
  const org = content.org ?? draft.published.org ?? { name: chapter.org.name, description: chapter.org.description, color: chapter.org.color, logo_asset_id: null };
  const setOrg = (patch: Partial<NonNullable<ChapterContent['org']>>) => change((c) => ({ ...c, org: { ...org, ...patch } }));
  const pickMosaic = (file: File) =>
    setCropping({
      file, kind: 'mosaic', newSource: true,
      apply: (asset, state, sourceId) => change((c) => ({ ...c, mosaic: { cols: c.mosaic?.cols ?? 2, rows: c.mosaic?.rows ?? 3, asset_id: asset.id, source_asset_id: sourceId ?? null, crop: state } })),
    });
  // Ajustar: reabre a imagem ORIGINAL com o enquadramento salvo (sem original, usa a já recortada).
  const adjustMosaic = async () => {
    try {
      const sourceUrl = assetUrl(mosaic?.source_asset_id ?? null);
      const url = sourceUrl ?? assetUrl(mosaic?.asset_id ?? null);
      if (!url) return;
      const blob = await (await fetch(url)).blob();
      const file = new File([blob], 'mosaico.jpg', { type: blob.type });
      setCropping({
        file, kind: 'mosaic', initial: sourceUrl ? mosaic?.crop ?? null : null, newSource: !sourceUrl,
        apply: (asset, state, sourceId) => change((c) => ({ ...c, mosaic: { ...c.mosaic!, asset_id: asset.id, source_asset_id: sourceId ?? c.mosaic?.source_asset_id ?? null, crop: state } })),
      });
    } catch {
      setError('Não foi possível abrir a imagem. Escolha o arquivo de novo.');
    }
  };

  // Peça do mosaico = figurinha como as outras. Sem texto próprio, vale o que o servidor gera.
  const pieceTotal = mosaic ? mosaic.cols * mosaic.rows : 0;
  const pieceOf = (index: number): MosaicPiece =>
    mosaic?.pieces?.find((p) => p.index === index) ?? {
      index, name: `${org.name}: peça ${index + 1} de ${pieceTotal}`, hint: `Peça ${index + 1} do mosaico`, back_text: org.description, back_links: [],
    };
  const setPieceFields = (index: number, patch: Partial<MosaicPiece>) =>
    change((c) => {
      if (!c.mosaic) return c;
      const others = (c.mosaic.pieces ?? []).filter((p) => p.index !== index);
      return { ...c, mosaic: { ...c.mosaic, pieces: [...others, { ...pieceOf(index), ...patch }].sort((a, b) => a.index - b.index) } };
    });
  const piecePreview = piece !== null && mosaic ? pieceOf(piece) : null;
  return (
    <div className="editor">
      {cropping ? (
        <ImageCropper file={cropping.file} spec={crop(cropping.kind, landscape, { cols: mosaic?.cols ?? 2, rows: mosaic?.rows ?? 3 })} initial={cropping.initial} onDone={uploadCropped} onCancel={() => setCropping(null)} />
      ) : null}
      {pieceSheet && piecePreview ? (
        <StickerSheet
          card={{
            id: -(1000 + piecePreview.index), number: piecePreview.index + 1, name: piecePreview.name, image_url: pieceImage, size: 'P', tag: 'mosaico',
            rarity: { name: 'mosaico', rank: 1, color: '#6b7280' }, org_id: chapter.org.id, org_name: org.name, org_color: org.color, page_no: 0,
            is_mosaic_piece: true, mosaic_index: piecePreview.index,
          }}
          preview={{ back_text: piecePreview.back_text, back_links: piecePreview.back_links }}
          onClose={() => setPieceSheet(false)}
        />
      ) : null}
      {inspect ? (() => {
        const sticker = content.stickers.find((item) => item.key === inspect);
        const card = cardOf(inspect);
        return sticker && card ? <StickerSheet card={card} preview={{ back_text: sticker.back_text, back_links: sticker.back_links }} onClose={() => setInspect(null)} /> : null;
      })() : null}
      <header className="editor-top">
        <Link to="/conta" className="editor-back">← Sair do editor</Link>
        <div>
          <p className="album-kicker">{chapter.album.title}</p>
          <h1>{content.org?.name || chapter.org.name}</h1>
        </div>
      </header>

      {revision?.review_note ? <p className="review-note"><b>Nota da revisão:</b> {revision.review_note}</p> : null}
      {chapter.closed ? <p className="notice">Álbum encerrado: o capítulo não pode mais mudar.</p> : null}
      {frozen && !chapter.closed ? <p className="notice">O álbum já começou: você pode mudar textos, imagens e posições e acrescentar figurinhas novas (número e raridade final são de quem publica), mas não remover nem desativar as que já existem.</p> : null}
      {locked && !chapter.closed ? <p className="notice">{revision ? STATUS[revision.status] : 'Somente leitura'}.</p> : null}
      <Message>{error}</Message>
      {note ? <Message kind="ok">{note}</Message> : null}

      <div className="segmented editor-tabs" role="tablist" aria-label="Seções do editor">
        {([['pages', 'Páginas'], ['general', 'Geral']] as const).map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{label}</button>
        ))}
      </div>

      {tab === 'pages' ? (
        <nav className="page-chips" aria-label="Páginas do capítulo">
          {pages.map((p, index) => (
            <button key={p.page_no} type="button" className={!mosaicPage && p.page_no === page.page_no ? 'active' : ''} onClick={() => { setMosaicPage(false); setPageIndex(index); setSelected(null); }}>{p.page_no}</button>
          ))}
          <button type="button" className="chip-add" disabled={locked || pages.length >= 30} onClick={() => { setMosaicPage(false); addPage(); }} aria-label="Nova página">+</button>
          <button type="button" className={`chip-mosaic${mosaicPage ? ' active' : ''}`} onClick={() => { setMosaicPage(true); setSelected(null); }}>▦ Mosaico</button>
        </nav>
      ) : null}

      {tab === 'pages' && !mosaicPage ? (
        <div className="editor-body">
          <div className="editor-canvas">
            <div className="editor-toolbar" role="toolbar" aria-label="Ferramentas da página">
              <button type="button" className="btn" disabled={locked || !depth.past} onClick={() => travel('past')}>↶ Desfazer</button>
              <button type="button" className="btn" disabled={locked || !depth.future} onClick={() => travel('future')}>↷ Refazer</button>
              <span className="spacer" />
              <button type="button" className={`btn${arrange ? ' on' : ''}`} disabled={locked} aria-pressed={arrange} onClick={() => setArrange((v) => !v)}>{arrange ? '✋ Mover: ligado' : '🔒 Mover: desligado'}</button>
              <button type="button" className="btn" disabled={locked || !differs} onClick={restoreAll}>Voltar ao original</button>
            </div>
            {arrange && !locked ? <p className="mode-banner">Modo mover ligado: arraste as figurinhas e puxe o canto para o tamanho. Desligue para só selecionar.</p> : null}
            <div className={`preview-stage edit-stage${landscape ? ' landscape' : ''}`}>
              {detail ? (
                <StickerPage
                  detail={detail}
                  onOpen={() => {}}
                  lastPasted={null}
                  overlay={<EditLayer stickers={onPage} selected={selected} head={selected === 'head'} landscape={landscape} locked={locked} arrange={arrange} onSelect={setSelected} onSlot={(key, slot) => moveSticker(key, slot)} />}
                />
              ) : null}
            </div>
            <p className="muted edit-tip">Toque numa figurinha para editar. Para mudar de lugar ou de tamanho, ligue o modo mover. Toque no topo para editar o texto da página.</p>
          </div>

          <div className="editor-inspector">
            <div className="admin-head">
              <h2>Figurinhas desta página ({onPage.length})</h2>
              <button type="button" className="btn" disabled={locked || content.stickers.length >= 200} onClick={addSticker}>+ Figurinha</button>
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
                <div className="sticker-peek">
                  {cardOf(current.key) ? <div className="peek-card"><StickerArt card={cardOf(current.key)!} /></div> : null}
                  <div className="photo-actions">
                    <button type="button" className="btn" onClick={() => setInspect(current.key)}>Ver e virar</button>
                    {published.has(current.key) ? <button type="button" className="btn" disabled={locked} onClick={() => restoreSticker(current.key)}>Voltar ao original</button> : null}
                    <small className="muted">Mostra a figurinha como o jogador vê, frente e verso.</small>
                  </div>
                </div>
                <div className="field"><span>Girar na página ({current.slot.rot ?? 0}°)</span>
                  <span className="zoom-row">
                    <button type="button" className="btn" disabled={locked} aria-label="Girar 5° para a esquerda" onClick={() => setRot(current, (current.slot.rot ?? 0) - 5)}>⟲</button>
                    <input type="range" min={-45} max={45} step={1} disabled={locked} value={clamp(current.slot.rot ?? 0, -45, 45)} onChange={(e) => setRot(current, Number(e.target.value))} aria-label="Ângulo da figurinha" />
                    <button type="button" className="btn" disabled={locked} aria-label="Girar 5° para a direita" onClick={() => setRot(current, (current.slot.rot ?? 0) + 5)}>⟳</button>
                    <button type="button" className="btn" disabled={locked || !current.slot.rot} onClick={() => setRot(current, 0)}>Zerar</button>
                  </span>
                </div>
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
                    <select disabled={locked || fixed(current.key)} value={current.suggested_rarity_id ?? ''} onChange={(e) => setSticker(current.key, { suggested_rarity_id: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Sem sugestão</option>
                      {rarities.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                    </select>
                    <small>É só uma sugestão: quem publica decide a raridade e o número.</small>
                  </label>
                  <label className="field check"><input type="checkbox" disabled={locked || fixed(current.key)} checked={current.active} onChange={(e) => setSticker(current.key, { active: e.target.checked })} /> <span>Aparece na página e nos pacotes</span></label>
                  {!fixed(current.key) ? <button type="button" className="link danger" disabled={locked} onClick={() => removeSticker(current.key)}>Remover figurinha</button> : null}
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

      {tab === 'pages' && mosaicPage ? (
        <div className="editor-body">
          <div className="editor-canvas">
            <label className="field check"><input type="checkbox" checked={mosaicLines} onChange={(e) => setMosaicLines(e.target.checked)} /> <span>Mostrar as linhas que dividem as figurinhas</span></label>
            <div className={`preview-stage edit-stage`}>
              {mosaicDetail ? <MosaicPage detail={mosaicDetail} onOpen={(slot) => setPieceIndex(slot.mosaic_index ?? null)} lastPasted={null} lines={mosaicLines} selected={piece} /> : <p className="empty-mosaic">Nenhuma imagem de mosaico ainda.</p>}
            </div>
          </div>
          <div className="editor-inspector">
            <section className="panel paper inspector">
              <h2>Mosaico</h2>
              <p className="muted">Uma imagem grande cortada em peças do tamanho de uma figurinha normal. A original fica guardada: use Ajustar imagem para aproximar, girar, espelhar e reenquadrar quando quiser, inclusive depois de mudar a grade.</p>
              <div className="photo-actions row">
                <PickButton primary label={mosaic?.asset_id ? 'Trocar imagem' : 'Escolher imagem'} disabled={locked || (frozen && mosaic?.asset_id == null)} onPick={pickMosaic} />
                {mosaic?.asset_id ? <button type="button" className="btn" disabled={locked} onClick={adjustMosaic}>Ajustar imagem</button> : null}
              </div>
              {mosaic?.asset_id && !frozen && !locked ? <button type="button" className="link danger" onClick={() => window.confirm('Remover o mosaico?') && change((c) => ({ ...c, mosaic: null }))}>Remover mosaico</button> : null}
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
            {mosaic?.asset_id ? (
              <section className="panel paper inspector" aria-label="Peças do mosaico">
                <h2>Peças</h2>
                <p className="muted">Cada peça é uma figurinha: nome, dica e verso próprios. Toque numa peça na imagem ou na lista.</p>
                <div className="piece-grid" style={{ gridTemplateColumns: `repeat(${Math.min(mosaic.cols, 5)}, 1fr)` }}>
                  {Array.from({ length: pieceTotal }, (_, index) => (
                    <button key={index} type="button" className={piece === index ? 'active' : ''} onClick={() => setPieceIndex(piece === index ? null : index)}>{index + 1}</button>
                  ))}
                </div>
                {piecePreview ? (
                  <>
                    <div className="sticker-peek">
                      {pieceImage ? <div className="peek-card"><div className="sticker rank-1"><img src={pieceImage} alt="" /></div></div> : null}
                      <div className="photo-actions">
                        <b>Peça {piecePreview.index + 1} de {pieceTotal}</b>
                        <button type="button" className="btn" onClick={() => setPieceSheet(true)}>Ver e virar</button>
                      </div>
                    </div>
                    <label className="field"><span>Nome</span>
                      <input maxLength={120} disabled={locked} value={piecePreview.name} onChange={(e) => setPieceFields(piecePreview.index, { name: e.target.value })} />
                    </label>
                    <label className="field"><span>Dica no espaço vazio</span>
                      <input maxLength={160} disabled={locked} value={piecePreview.hint} onChange={(e) => setPieceFields(piecePreview.index, { hint: e.target.value })} />
                    </label>
                    <label className="field"><span>Texto do verso <Counter value={piecePreview.back_text} max={2000} /></span>
                      <textarea rows={4} maxLength={2000} disabled={locked} value={piecePreview.back_text} onChange={(e) => setPieceFields(piecePreview.index, { back_text: e.target.value })} />
                    </label>
                    <div className="field"><span>Links no verso</span>
                      {piecePreview.back_links.map((link, i) => (
                        <div key={i} className="link-row">
                          <input aria-label={`Nome do link ${i + 1}`} placeholder="Nome" maxLength={40} disabled={locked} value={link.label}
                            onChange={(e) => setPieceFields(piecePreview.index, { back_links: piecePreview.back_links.map((l, j) => (j === i ? { ...l, label: e.target.value } : l)) })} />
                          <input aria-label={`Endereço do link ${i + 1}`} type="url" placeholder="https://…" maxLength={300} disabled={locked} value={link.url}
                            onChange={(e) => setPieceFields(piecePreview.index, { back_links: piecePreview.back_links.map((l, j) => (j === i ? { ...l, url: e.target.value } : l)) })} />
                          <button type="button" className="link danger" disabled={locked} onClick={() => setPieceFields(piecePreview.index, { back_links: piecePreview.back_links.filter((_, j) => j !== i) })}>Tirar</button>
                        </div>
                      ))}
                      {piecePreview.back_links.length < MAX_LINKS ? (
                        <button type="button" className="link" disabled={locked} onClick={() => setPieceFields(piecePreview.index, { back_links: [...piecePreview.back_links, { label: '', url: 'https://' }] })}>+ Adicionar link</button>
                      ) : null}
                    </div>
                  </>
                ) : null}
              </section>
            ) : null}
          </div>
        </div>
      ) : null}

      {tab === 'general' ? (
        <>
          <section className="panel paper inspector">
            <h2>Sobre a organização</h2>
            <p className="muted">Aparece na capa do álbum e no início do capítulo. Vale para todos os álbuns desta organização.</p>
            <div className="org-logo-row">
              {assetUrl(org.logo_asset_id) ? <img className="org-logo-preview" src={assetUrl(org.logo_asset_id)!} alt="Logo atual" style={{ borderColor: org.color }} /> : <i className="org-logo-preview" style={{ borderColor: org.color }} />}
              <div className="photo-actions">
                <PickButton label={org.logo_asset_id ? 'Trocar logo' : 'Escolher logo'} disabled={locked} onPick={(file) => setCropping({ file, kind: 'logo', apply: (asset) => setOrg({ logo_asset_id: asset.id }) })} />
                {org.logo_asset_id ? <button type="button" className="link danger" disabled={locked} onClick={() => setOrg({ logo_asset_id: null })}>Tirar logo</button> : null}
              </div>
            </div>
            <label className="field">Nome
              <input value={org.name} maxLength={120} disabled={locked} onChange={(e) => setOrg({ name: e.target.value })} />
            </label>
            <label className="field"><span>Descrição <Counter value={org.description} max={600} /></span>
              <textarea rows={3} value={org.description} maxLength={600} disabled={locked} onChange={(e) => setOrg({ description: e.target.value })} />
            </label>
            <label className="field">Cor da organização
              <span className="color-row">
                <input type="color" value={org.color} disabled={locked} onChange={(e) => setOrg({ color: e.target.value })} aria-label="Escolher cor" />
                <code>{org.color}</code>
              </span>
            </label>
          </section>
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
        </>
      ) : null}

      <footer className="editor-bar">
        <span className={`save-state save-${save.state}`} role="status">{saveLabel}</span>
        {save.state === 'error' ? <button className="btn" onClick={persist}>Tentar de novo</button> : null}
        {locked && !chapter.closed && revision && (revision.status === 'submitted' || revision.status === 'approved') ? (
          <button className="btn" onClick={() => window.confirm('Retirar o envio? A revisão sai da fila e você pode editar de novo.') && withdraw()}>Retirar envio</button>
        ) : null}
        <button className="btn btn-primary" disabled={locked || save.state === 'saving' || (!revision && save.state === 'idle')} onClick={submit}>Enviar para revisão</button>
      </footer>
    </div>
  );
}
