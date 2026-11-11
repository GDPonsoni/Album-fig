import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, post, type AlbumIndex, type Card, type PageDetail, type PageMeta, type Slot } from '../api';
import { useAuth } from '../auth';
import { StickerSheet } from '../components/StickerSheet';
import { Message, StickerArt, VerifyBanner } from '../components/ui';
import { lowFx, buzz } from '../fx';

const pageKey = (page: { org_id: number; page_no: number }) => `${page.org_id}:${page.page_no}`;

function CoverPage({ index, goTo }: { index: AlbumIndex; goTo: (page: number) => void }) {
  const percent = index.total ? Math.round((100 * index.filled) / index.total) : 0;
  return (
    <article className="album-page cover">
      <div className="cover-band">
        <p className="cover-kicker">Álbum de figurinhas</p>
        <h1 className="cover-title">{index.album?.title ?? 'Álbum'}</h1>
      </div>
      <div className="cover-progress">
        <strong>{percent}%</strong>
        <span>{index.filled} de {index.total} coladas</span>
        <div className="bar"><i style={{ width: `${percent}%` }} /></div>
      </div>
      <ul className="cover-orgs">
        {index.orgs.map((org) => {
          const page = index.pages.findIndex((p) => p.org_id === org.id);
          return (
            <li key={org.id}>
              <button onClick={() => goTo(page + 1)}>
                {org.logo_url ? <img src={org.logo_url} alt="" loading="lazy" /> : <i style={{ background: org.color }} />}
                <span>{org.name}</span>
                <small>{org.filled}/{org.total}</small>
              </button>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

function EmptySlot({ slot }: { slot: Slot }) {
  return (
    <div className="slot-empty">
      <svg viewBox="0 0 60 80" aria-hidden="true">
        <circle cx="30" cy="30" r="13" />
        <path d="M6 80c0-20 11-30 24-30s24 10 24 30z" />
      </svg>
      <b>{slot.number}</b>
      {slot.hint ? <small>{slot.hint}</small> : null}
    </div>
  );
}

function StickerPage({ detail, onOpen, lastPasted }: { detail: PageDetail; onOpen: (slot: Slot) => void; lastPasted: number | null }) {
  return (
    <article className="album-page paper" style={{ '--org': detail.org.color } as React.CSSProperties}>
      <header className="page-head">
        {detail.org.logo_url ? <img src={detail.org.logo_url} alt="" /> : null}
        <div>
          <h2>{detail.org.name}</h2>
          {detail.page_no === 1 ? <p>{detail.org.description}</p> : null}
        </div>
      </header>
      {detail.slots.map((slot) => (
        <button
          key={slot.sticker_id}
          className={`slot${lastPasted === slot.sticker_id ? ' just-pasted' : ''}`}
          data-slot={slot.sticker_id}
          disabled={!slot.filled}
          aria-label={slot.filled ? `Ver verso de ${slot.card!.name}` : `Espaço vazio ${slot.number}`}
          style={{ left: `${slot.layout.x}%`, top: `${slot.layout.y}%`, width: `${slot.layout.w}%`, height: `${slot.layout.h}%` }}
          onClick={() => onOpen(slot)}
        >
          {slot.filled ? <StickerArt card={slot.card!} /> : <EmptySlot slot={slot} />}
        </button>
      ))}
    </article>
  );
}

function MosaicPage({ detail, onOpen, lastPasted }: { detail: PageDetail; onOpen: (slot: Slot) => void; lastPasted: number | null }) {
  const { org } = detail;
  const filled = detail.slots.filter((s) => s.filled).length;
  return (
    <article className="album-page paper" style={{ '--org': org.color } as React.CSSProperties}>
      <header className="page-head">
        <div>
          <h2>Mosaico</h2>
          <p>{org.name}: {filled} de {detail.slots.length} peças</p>
        </div>
      </header>
      <div
        className="mosaic"
        style={{ gridTemplateColumns: `repeat(${org.mosaic_cols}, 1fr)`, gridTemplateRows: `repeat(${org.mosaic_rows}, 1fr)`, aspectRatio: `${2} / ${3}` }}
      >
        {org.mosaic_image_url ? <img className="mosaic-faded" src={org.mosaic_image_url} alt="" loading="lazy" /> : null}
        {detail.slots.map((slot) => {
          const index = slot.mosaic_index ?? 0;
          const col = index % org.mosaic_cols;
          const row = Math.floor(index / org.mosaic_cols);
          const x = org.mosaic_cols > 1 ? (col / (org.mosaic_cols - 1)) * 100 : 0;
          const y = org.mosaic_rows > 1 ? (row / (org.mosaic_rows - 1)) * 100 : 0;
          return (
            <button
              key={slot.sticker_id}
              data-slot={slot.sticker_id}
              disabled={!slot.filled}
              aria-label={slot.filled ? `Peça ${index + 1} colada` : `Peça ${index + 1}, espaço ${slot.number}`}
              className={`mosaic-piece${slot.filled ? ' filled' : ''}${lastPasted === slot.sticker_id ? ' just-pasted' : ''}`}
              style={
                slot.filled
                  ? { backgroundImage: `url(${org.mosaic_image_url})`, backgroundSize: `${org.mosaic_cols * 100}% ${org.mosaic_rows * 100}%`, backgroundPosition: `${x}% ${y}%` }
                  : undefined
              }
              onClick={() => onOpen(slot)}
            >
              {slot.filled ? null : <b>{slot.number}</b>}
            </button>
          );
        })}
      </div>
    </article>
  );
}

/** Faixa com rolagem lateral. No toque a rolagem é nativa; com mouse, arrasta ou usa as setas. */
function TrayRow({ children }: { children: React.ReactNode }) {
  const row = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  const measure = useCallback(() => {
    const el = row.current;
    if (el) setEdges({ start: el.scrollLeft > 2, end: el.scrollLeft + el.clientWidth < el.scrollWidth - 2 });
  }, []);
  useEffect(() => {
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [measure, children]);

  const scrollBy = (direction: number) => {
    const el = row.current;
    el?.scrollBy({ left: direction * el.clientWidth * 0.8, behavior: lowFx() ? 'auto' : 'smooth' });
  };
  const onPointerDown = (event: ReactPointerEvent) => {
    if (event.pointerType === 'mouse' && row.current) drag.current = { x: event.clientX, left: row.current.scrollLeft, moved: false };
  };
  const onPointerMove = (event: ReactPointerEvent) => {
    const state = drag.current;
    if (!state || !row.current) return;
    const dx = event.clientX - state.x;
    if (Math.abs(dx) > 5) state.moved = true;
    if (state.moved) row.current.scrollLeft = state.left - dx;
  };
  return (
    <div className={`tray-scroller${edges.start ? ' more-start' : ''}${edges.end ? ' more-end' : ''}`}>
      <div
        className="tray-row"
        ref={row}
        onScroll={measure}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => { window.setTimeout(() => { drag.current = null; }); }}
        onPointerLeave={() => { drag.current = null; }}
        onClickCapture={(event) => {
          // Soltar depois de arrastar não pode colar a figurinha que estava sob o mouse.
          if (drag.current?.moved) {
            event.preventDefault();
            event.stopPropagation();
          }
        }}
      >
        {children}
      </div>
      {edges.start ? <button className="tray-arrow tray-arrow-start" aria-label="Figurinhas anteriores" onClick={() => scrollBy(-1)}>‹</button> : null}
      {edges.end ? <button className="tray-arrow tray-arrow-end" aria-label="Mais figurinhas" onClick={() => scrollBy(1)}>›</button> : null}
    </div>
  );
}

/** Sem `albumId` é o álbum em andamento; com ele, um álbum da conta (encerrado abre só para leitura). */
export function AlbumPage({ albumId }: { albumId?: number }) {
  const { user } = useAuth();
  const base = albumId ? `/albums/${albumId}` : '/album';
  const root = albumId ? `/albuns/${albumId}` : '/';
  const [index, setIndex] = useState<AlbumIndex | null>(null);
  const [details, setDetails] = useState<Record<string, PageDetail>>({});
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const current = Math.max(0, Math.floor(Number(params.get('pagina'))) || 0);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<Card | null>(null);
  const [lastPasted, setLastPasted] = useState<number | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const detailsRef = useRef(details);
  detailsRef.current = details;

  const pages: (PageMeta | null)[] = useMemo(() => (index ? [null, ...index.pages] : [null]), [index]);
  const lastIndex = pages.length - 1;
  const safeCurrent = Math.min(current, lastIndex);

  const loadIndex = useCallback(() => api<AlbumIndex>(base).then(setIndex), [base]);
  const loadPage = useCallback(async (page: PageMeta) => {
    const detail = await api<PageDetail>(`${base}/orgs/${page.org_id}/pages/${page.page_no}`);
    setDetails((all) => ({ ...all, [pageKey(page)]: detail }));
    return detail;
  }, [base]);

  useEffect(() => {
    loadIndex().catch((err) => setError(err.message));
  }, [loadIndex]);

  // Só a página atual e as vizinhas são pedidas ao servidor.
  useEffect(() => {
    for (const position of [safeCurrent, safeCurrent - 1, safeCurrent + 1]) {
      const page = pages[position];
      if (page && !detailsRef.current[pageKey(page)]) loadPage(page).catch(() => {});
    }
  }, [safeCurrent, pages, loadPage]);

  // A página fica na URL. Sair da capa empilha no histórico e folhear não, então
  // o voltar do celular leva de qualquer página para a capa, e não para fora do app.
  const cameFromCover = (location.state as { fromCover?: boolean } | null)?.fromCover === true;
  const goToPage = useCallback(
    (target: number) => {
      if (target === 0) {
        if (cameFromCover) navigate(-1);
        else navigate(root, { replace: true });
      } else {
        navigate(`${root}?pagina=${target}`, safeCurrent === 0 ? { state: { fromCover: true } } : { replace: true, state: location.state });
      }
    },
    [cameFromCover, location.state, navigate, root, safeCurrent],
  );

  const animating = useRef(false);
  const settle = useRef<(() => void) | null>(null);
  useLayoutEffect(() => {
    settle.current?.();
    settle.current = null;
  }, [safeCurrent]);

  const slideTo = useCallback(
    (target: number) =>
      new Promise<void>((resolve) => {
        const track = trackRef.current;
        const direction = Math.sign(target - safeCurrent);
        if (!track || direction === 0 || lowFx() || Math.abs(target - safeCurrent) > 1) {
          goToPage(target);
          resolve();
          return;
        }
        animating.current = true;
        const animation = track.animate(
          [{ transform: track.style.transform || 'translateX(0)' }, { transform: `translateX(${-direction * 100}%)` }],
          { duration: 240, easing: 'cubic-bezier(.2,.7,.2,1)', fill: 'forwards' },
        );
        animation.onfinish = () => {
          // Só desfaz o deslize quando a nova página já está na tela, para não piscar.
          settle.current = () => {
            track.style.transform = '';
            animation.cancel();
            animating.current = false;
            resolve();
          };
          goToPage(target);
        };
      }),
    [goToPage, safeCurrent],
  );

  // Swipe: arrasta a faixa com a página e as vizinhas; passou do limite, vira.
  const drag = useRef<{ x: number; y: number; id: number; horizontal: boolean | null } | null>(null);
  const onPointerDown = (event: ReactPointerEvent) => {
    if (animating.current) return;
    drag.current = { x: event.clientX, y: event.clientY, id: event.pointerId, horizontal: null };
  };
  const onPointerMove = (event: ReactPointerEvent) => {
    const state = drag.current;
    if (!state || state.id !== event.pointerId) return;
    const dx = event.clientX - state.x;
    const dy = event.clientY - state.y;
    if (state.horizontal === null && Math.hypot(dx, dy) > 8) {
      state.horizontal = Math.abs(dx) > Math.abs(dy);
      if (state.horizontal) (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    }
    if (state.horizontal && trackRef.current) {
      const blocked = (dx > 0 && safeCurrent === 0) || (dx < 0 && safeCurrent === lastIndex);
      trackRef.current.style.transform = `translateX(${blocked ? dx / 5 : dx}px)`;
    }
  };
  const onPointerEnd = (event: ReactPointerEvent) => {
    const state = drag.current;
    drag.current = null;
    const track = trackRef.current;
    if (!state?.horizontal || !track) return;
    const dx = event.clientX - state.x;
    const target = safeCurrent + (dx < 0 ? 1 : -1);
    if (Math.abs(dx) > track.clientWidth * 0.18 && target >= 0 && target <= lastIndex) {
      slideTo(target);
    } else {
      track.style.transform = '';
    }
  };

  const openSlot = useCallback((slot: Slot) => {
    if (slot.filled && slot.card) setOpen(slot.card);
  }, []);
  const closeBack = useCallback(() => setOpen(null), []);

  // Colar é otimista: o voo começa no toque e o POST corre em paralelo. O que foi
  // colado localmente some da bandeja até o servidor confirmar (ou recusar).
  const [hidden, setHidden] = useState<Set<number>>(() => new Set());
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const animBusy = useRef(false);
  const flights = useRef(new Map<number, Flight>());
  const failed = useRef(new Set<number>());
  const pendingPosts = useRef(0);
  const dirty = useRef(new Map<string, PageMeta>());
  const reconcileRun = useRef(0);

  const hide = (ids: number[]) => setHidden((all) => new Set([...all, ...ids]));

  const applyPasted = useCallback((cards: Card[]) => {
    const ids = new Map(cards.map((card) => [card.id, card]));
    const keyOf = (card: Card) => `${card.org_id}:${card.is_mosaic_piece ? 0 : card.page_no}`;
    setDetails((all) => {
      const next = { ...all };
      for (const [key, detail] of Object.entries(all)) {
        if (!detail.slots.some((slot) => ids.has(slot.sticker_id) && !slot.filled)) continue;
        next[key] = {
          ...detail,
          slots: detail.slots.map((slot) => (ids.has(slot.sticker_id) ? { ...slot, filled: true, card: ids.get(slot.sticker_id) } : slot)),
        };
      }
      return next;
    });
    setIndex((current) => {
      if (!current) return current;
      const perPage = new Map<string, number>();
      const perOrg = new Map<number, number>();
      for (const card of cards) {
        perPage.set(keyOf(card), (perPage.get(keyOf(card)) ?? 0) + 1);
        perOrg.set(card.org_id, (perOrg.get(card.org_id) ?? 0) + 1);
      }
      return {
        ...current,
        filled: current.filled + cards.length,
        pages: current.pages.map((p) => ({ ...p, filled: p.filled + (perPage.get(pageKey(p)) ?? 0) })),
        orgs: current.orgs.map((o) => ({ ...o, filled: o.filled + (perOrg.get(o.id) ?? 0) })),
      };
    });
  }, []);

  // Só troca o estado local pelo do servidor quando não há POST nem voo em curso: uma resposta
  // antiga não pode desfazer na tela uma colagem mais recente, e o estado do servidor não pode
  // chegar antes do pouso (a figurinha apareceria no espaço no meio do voo e contaria em dobro).
  const reconcile = useCallback(async () => {
    if (pendingPosts.current > 0 || animBusy.current) return;
    const run = ++reconcileRun.current;
    const metas = [...dirty.current.entries()];
    try {
      const [fresh, ...pagesData] = await Promise.all([
        api<AlbumIndex>(base),
        ...metas.map(([, page]) => api<PageDetail>(`${base}/orgs/${page.org_id}/pages/${page.page_no}`).catch(() => null)),
      ]);
      if (pendingPosts.current > 0 || animBusy.current || run !== reconcileRun.current) return;
      setIndex(fresh);
      setDetails((all) => {
        const next = { ...all };
        metas.forEach(([key], i) => {
          const detail = pagesData[i];
          if (detail) next[key] = detail;
        });
        return next;
      });
      for (const [key] of metas) dirty.current.delete(key);
      failed.current.clear();
      setHidden(new Set());
    } catch {
      // Sem rede: o estado otimista fica até a próxima reconciliação.
    }
  }, [base]);

  const track = useCallback(
    (request: Promise<unknown>, ids: number[]) => {
      pendingPosts.current += 1;
      request
        .catch((err: Error) => {
          for (const id of ids) {
            failed.current.add(id);
            flights.current.get(id)?.cancel();
          }
          setError(err.message);
        })
        .finally(() => {
          pendingPosts.current -= 1;
          reconcile();
        });
    },
    [reconcile],
  );

  // As páginas de destino da bandeja já ficam carregadas, para o voo não esperar a rede.
  const requested = useRef(new Set<string>());
  useEffect(() => {
    if (!index) return;
    const wanted = new Map<string, PageMeta>();
    for (const card of index.tray) {
      const page = index.pages.find((p) => p.org_id === card.org_id && p.page_no === (card.is_mosaic_piece ? 0 : card.page_no));
      if (page) wanted.set(pageKey(page), page);
      if (wanted.size >= 8) break;
    }
    for (const [key, page] of wanted) {
      if (detailsRef.current[key] || requested.current.has(key)) continue;
      requested.current.add(key);
      loadPage(page).catch(() => requested.current.delete(key));
    }
  }, [index, loadPage]);

  const paste = (card: Card, tile: HTMLElement) => {
    if (animBusy.current || hiddenRef.current.has(card.id) || !index) return;
    const target = index.pages.findIndex((p) => p.org_id === card.org_id && p.page_no === (card.is_mosaic_piece ? 0 : card.page_no));
    if (target < 0) return;
    const position = target + 1;
    const meta = index.pages[target];
    const key = pageKey(meta);
    const from = tile.getBoundingClientRect();
    animBusy.current = true;
    setError('');
    hide([card.id]);
    dirty.current.set(key, meta);
    track(post(`/album/paste/${card.id}`), [card.id]);

    void (async () => {
      try {
        let moved = false;
        if (position !== safeCurrent) {
          await slideTo(position);
          moved = true;
        }
        if (!detailsRef.current[key]) {
          await loadPage(meta).catch(() => {});
          moved = true;
        }
        if (moved) await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        if (failed.current.has(card.id)) return;
        const flight = flyToSlot(card, from);
        flights.current.set(card.id, flight);
        const landed = await flight.finished;
        flights.current.delete(card.id);
        if (!landed || failed.current.has(card.id)) return;
        applyPasted([card]);
        setLastPasted(card.id);
        buzz(15);
        window.setTimeout(() => setLastPasted(null), 500);
      } finally {
        animBusy.current = false;
        reconcile(); // se o servidor já respondeu, é agora que o estado dele entra
      }
    })();
  };

  const pasteAll = () => {
    if (animBusy.current || !index) return;
    const cards = index.tray.filter((card) => !hiddenRef.current.has(card.id));
    if (!cards.length) return;
    const keyOf = (card: Card) => `${card.org_id}:${card.is_mosaic_piece ? 0 : card.page_no}`;
    const here = pages[safeCurrent];
    const hereKey = here ? pageKey(here) : '';
    // Toda a geometria é lida antes de qualquer escrita no DOM.
    const viewport = window.innerWidth;
    const origins = new Map<number, DOMRect>();
    if (!lowFx()) {
      document.querySelectorAll<HTMLElement>('.tray-tile[data-tray]').forEach((tile) => {
        const rect = tile.getBoundingClientRect();
        if (rect.right > 0 && rect.left < viewport) origins.set(Number(tile.dataset.tray), rect);
      });
    }
    const stage = trackRef.current?.getBoundingClientRect();

    animBusy.current = true;
    setError('');
    const ids = cards.map((card) => card.id);
    hide(ids);
    for (const page of index.pages) {
      const key = pageKey(page);
      if (detailsRef.current[key] && cards.some((card) => keyOf(card) === key)) dirty.current.set(key, page);
    }
    track(post('/album/paste-all'), ids);

    // No máximo MAX_FLIGHTS voos, em sequência curta; o resto aparece de uma vez.
    const visible = cards.filter((card) => origins.has(card.id));
    const chosen = [...visible.filter((card) => keyOf(card) === hereKey), ...visible.filter((card) => keyOf(card) !== hereKey)].slice(0, MAX_FLIGHTS);
    const running = chosen.map((card, i) => {
      const from = origins.get(card.id)!;
      const flight = keyOf(card) === hereKey || !stage ? flyToSlot(card, from, i * FLIGHT_STEP) : flyIntoAlbum(card, from, stage, i * FLIGHT_STEP);
      flights.current.set(card.id, flight);
      return flight.finished;
    });
    void Promise.all(running).then((landed) => {
      for (const id of ids) flights.current.delete(id);
      animBusy.current = false;
      if (!landed.includes(false) && !ids.some((id) => failed.current.has(id))) {
        applyPasted(cards);
        buzz([15, 30, 15]);
      }
      reconcile();
    });
  };

  if (!index) {
    return <div className="screen">{error ? <Message>{error}</Message> : <div className="album-skeleton" />}</div>;
  }

  if (!index.album) {
    return (
      <div className="screen">
        <h1 className="screen-title">Álbum</h1>
        <section className="panel paper empty-album">
          <h2>Nenhum álbum em andamento</h2>
          <p className="muted">Quando o próximo álbum começar, ele aparece aqui. Os álbuns que você já jogou continuam na sua conta.</p>
          <Link className="btn btn-primary" to="/albuns">Meus álbuns</Link>
        </section>
      </div>
    );
  }

  const readOnly = index.read_only;
  const meta = pages[safeCurrent];
  const trayCards = index.tray.filter((card) => !hidden.has(card.id));
  const renderPage = (position: number) => {
    if (position === 0) return <CoverPage index={index} goTo={(page) => slideTo(page)} />;
    const page = pages[position];
    const detail = page ? details[pageKey(page)] : null;
    if (!detail) return <article className="album-page paper page-loading" />;
    return detail.kind === 'mosaic' ? (
      <MosaicPage detail={detail} onOpen={openSlot} lastPasted={lastPasted} />
    ) : (
      <StickerPage detail={detail} onOpen={openSlot} lastPasted={lastPasted} />
    );
  };

  return (
    <div className="album">
      {!user?.verified && !readOnly ? <VerifyBanner /> : null}
      <div className="album-bar">
        {safeCurrent > 0 ? (
          <button className="back-to-cover" onClick={() => goToPage(0)}>
            <span aria-hidden="true">←</span> Capa
          </button>
        ) : null}
        <div className="album-bar-text">
          <strong>{meta ? meta.org_name : 'Capa'}</strong>
          <small>{meta ? `${meta.kind === 'mosaic' ? 'Mosaico' : `Página ${meta.page_no}`} · ${meta.filled}/${meta.total}` : `${index.filled} de ${index.total} figurinhas`}</small>
        </div>
        {safeCurrent > 0 ? (
          <button className="round" aria-label="Página anterior" onClick={() => slideTo(safeCurrent - 1)}>‹</button>
        ) : null}
        <button className="round" aria-label="Próxima página" disabled={safeCurrent === lastIndex} onClick={() => slideTo(safeCurrent + 1)}>›</button>
      </div>

      <div className="album-stage" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerEnd} onPointerCancel={onPointerEnd}>
        <div className="album-track" ref={trackRef}>
          {[-1, 0, 1].map((offset) => {
            const position = safeCurrent + offset;
            if (position < 0 || position > lastIndex) return null;
            return (
              <div key={position} className="album-panel" style={{ left: `${offset * 100}%` }}>
                {renderPage(position)}
              </div>
            );
          })}
        </div>
      </div>

      <Message>{error}</Message>

      {readOnly ? (
        <section className="tray tray-empty" aria-label="Álbum encerrado">
          <p className="tray-label">Álbum encerrado · somente leitura. <Link to="/albuns">Meus álbuns</Link></p>
        </section>
      ) : (
        <section className={`tray${trayCards.length ? '' : ' tray-empty'}`} aria-label="Bandeja de figurinhas para colar">
          {trayCards.length ? (
            <>
              <div className="tray-head">
                <p className="tray-label"><b>{trayCards.length}</b> para colar · toque para colar, deslize para ver mais</p>
                {trayCards.length > 1 ? (
                  <button className="tray-all" disabled={!user?.verified} onClick={pasteAll}>Colar tudo</button>
                ) : null}
              </div>
              <TrayRow>
                {trayCards.map((card) => (
                  <button
                    key={card.id}
                    className="tray-tile"
                    data-tray={card.id}
                    aria-label={`Colar ${card.name}`}
                    disabled={!user?.verified}
                    onClick={(event) => paste(card, event.currentTarget)}
                  >
                    <StickerArt card={card} />
                  </button>
                ))}
              </TrayRow>
            </>
          ) : (
            <p className="tray-label">{hidden.size ? 'Colando…' : 'Bandeja vazia. Abra um pacote para ganhar figurinhas.'}</p>
          )}
        </section>
      )}

      {open ? <StickerSheet card={open} onClose={closeBack} /> : null}
    </div>
  );
}

const MAX_FLIGHTS = 12;
const FLIGHT_STEP = 70; // ms entre um voo e o seguinte no "Colar tudo"

interface Flight {
  finished: Promise<boolean>; // false quando o voo foi cancelado
  cancel: () => void;
}

const afterPaint = (run: () => void) => requestAnimationFrame(() => requestAnimationFrame(run));

/** A figurinha sai da bandeja e voa até o espaço. Lê a geometria uma vez e só anima transform. */
function flyToSlot(card: Card, from: DOMRect, delay = 0): Flight {
  const slot = document.querySelector<HTMLElement>(`.album-panel [data-slot="${card.id}"]`);
  if (!slot || lowFx() || !card.image_url) return { finished: Promise.resolve(true), cancel: () => {} };
  const to = slot.getBoundingClientRect();
  const clone = document.createElement('img');
  clone.src = card.image_url;
  clone.className = 'flying';
  Object.assign(clone.style, { left: `${to.left}px`, top: `${to.top}px`, width: `${to.width}px`, height: `${to.height}px` });
  document.body.append(clone);
  const scale = from.width / to.width;
  const dx = from.left - to.left;
  const dy = from.top - to.top;
  const animation = clone.animate(
    [
      { transform: `translate(${dx}px, ${dy}px) scale(${scale})` },
      { transform: `translate(${dx * 0.4}px, ${dy * 0.45 - 30}px) scale(${Math.max(scale, 1) * 1.1}) rotate(-5deg)`, offset: 0.55 },
      { transform: 'translate(0, 0) scale(1.08) rotate(1deg)', offset: 0.88 },
      { transform: 'translate(0, 0) scale(1)' },
    ],
    { duration: 560, delay, easing: 'cubic-bezier(.3,.6,.3,1)', fill: 'backwards' },
  );
  return {
    // O clone só sai depois que o espaço preenchido já foi pintado, para não piscar.
    finished: animation.finished.then(
      () => {
        afterPaint(() => clone.remove());
        return true;
      },
      () => {
        clone.remove();
        return false;
      },
    ),
    cancel: () => animation.cancel(),
  };
}

/** No "Colar tudo", a figurinha de outra página voa para dentro do álbum e some. */
function flyIntoAlbum(card: Card, from: DOMRect, stage: DOMRect, delay: number): Flight {
  if (lowFx() || !card.image_url) return { finished: Promise.resolve(true), cancel: () => {} };
  const clone = document.createElement('img');
  clone.src = card.image_url;
  clone.className = 'flying';
  Object.assign(clone.style, { left: `${from.left}px`, top: `${from.top}px`, width: `${from.width}px`, height: `${from.height}px`, opacity: '0' });
  document.body.append(clone);
  const dx = stage.left + stage.width / 2 - (from.left + from.width / 2);
  const dy = stage.top + stage.height / 2 - (from.top + from.height / 2);
  const animation = clone.animate(
    [
      { transform: 'translate(0, 0) scale(1)', opacity: 1 },
      { transform: `translate(${dx * 0.6}px, ${dy * 0.7 - 20}px) scale(1.15) rotate(-6deg)`, opacity: 1, offset: 0.6 },
      { transform: `translate(${dx}px, ${dy}px) scale(0.35)`, opacity: 0 },
    ],
    { duration: 480, delay, easing: 'cubic-bezier(.3,.6,.3,1)', fill: 'backwards' },
  );
  const done = (landed: boolean) => {
    clone.remove();
    return landed;
  };
  return { finished: animation.finished.then(() => done(true), () => done(false)), cancel: () => animation.cancel() };
}

/** Rota /albuns/:id: um álbum da conta. Trocar de álbum remonta a tela, para não misturar estado. */
export function AlbumByIdPage() {
  const id = Number(useParams().id);
  return Number.isInteger(id) && id > 0 ? <AlbumPage key={id} albumId={id} /> : <AlbumPage />;
}
