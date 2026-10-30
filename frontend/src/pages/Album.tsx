import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { api, post, type AlbumIndex, type Card, type PageDetail, type PageMeta, type Slot } from '../api';
import { useAuth } from '../auth';
import { Message, StickerArt, VerifyBanner } from '../components/ui';
import { lowFx, buzz } from '../fx';

const pageKey = (page: { org_id: number; page_no: number }) => `${page.org_id}:${page.page_no}`;
const INDEX_KEY = 'album:page';

function CoverPage({ index, goTo }: { index: AlbumIndex; goTo: (page: number) => void }) {
  const percent = index.total ? Math.round((100 * index.filled) / index.total) : 0;
  return (
    <article className="album-page cover">
      <div className="cover-band">
        <p className="cover-kicker">Calourada</p>
        <h1 className="cover-title">Álbum de figurinhas</h1>
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

function BackSheet({ card, onClose }: { card: Card; onClose: () => void }) {
  const [back, setBack] = useState<{ back_text: string; back_links: { label: string; url: string }[] } | null>(null);
  const [flipped, setFlipped] = useState(false);
  useEffect(() => {
    api(`/stickers/${card.id}/back`).then(setBack).catch(onClose);
    const timer = window.setTimeout(() => setFlipped(true), lowFx() ? 0 : 280);
    return () => window.clearTimeout(timer);
  }, [card.id, onClose]);
  return (
    <div className="overlay" onClick={onClose}>
      <div className={`flip${flipped ? ' flipped' : ''}`} onClick={(event) => event.stopPropagation()}>
        <div className="flip-face"><StickerArt card={card} /></div>
        <div className="flip-face flip-back paper">
          <p className="flip-org" style={{ color: card.org_color }}>{card.org_name}</p>
          <h3>{card.name}</h3>
          <p className="flip-rarity" style={{ color: card.rarity.color }}>#{card.number} · {card.rarity.name} · {card.tag}</p>
          <p>{back?.back_text}</p>
          <div className="flip-links">
            {back?.back_links.map((link) => (
              <a key={link.url} href={link.url} target="_blank" rel="noopener noreferrer">{link.label}</a>
            ))}
          </div>
          <button className="btn" onClick={onClose}>Fechar</button>
        </div>
      </div>
    </div>
  );
}

export function AlbumPage() {
  const { user } = useAuth();
  const [index, setIndex] = useState<AlbumIndex | null>(null);
  const [details, setDetails] = useState<Record<string, PageDetail>>({});
  const [current, setCurrent] = useState(() => {
    try {
      return Number(sessionStorage.getItem(INDEX_KEY)) || 0;
    } catch {
      return 0;
    }
  });
  const [error, setError] = useState('');
  const [open, setOpen] = useState<Card | null>(null);
  const [lastPasted, setLastPasted] = useState<number | null>(null);
  const [flying, setFlying] = useState<number | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const detailsRef = useRef(details);
  detailsRef.current = details;

  const pages: (PageMeta | null)[] = useMemo(() => (index ? [null, ...index.pages] : [null]), [index]);
  const lastIndex = pages.length - 1;
  const safeCurrent = Math.min(current, lastIndex);

  const loadIndex = useCallback(() => api<AlbumIndex>('/album').then(setIndex), []);
  const loadPage = useCallback(async (page: PageMeta) => {
    const detail = await api<PageDetail>(`/album/orgs/${page.org_id}/pages/${page.page_no}`);
    setDetails((all) => ({ ...all, [pageKey(page)]: detail }));
    return detail;
  }, []);

  useEffect(() => {
    loadIndex().catch((err) => setError(err.message));
  }, [loadIndex]);

  // RF-35: só a página atual e as vizinhas são pedidas ao servidor.
  useEffect(() => {
    for (const position of [safeCurrent, safeCurrent - 1, safeCurrent + 1]) {
      const page = pages[position];
      if (page && !detailsRef.current[pageKey(page)]) loadPage(page).catch(() => {});
    }
    try {
      sessionStorage.setItem(INDEX_KEY, String(safeCurrent));
    } catch {
      /* preferência de conveniência */
    }
  }, [safeCurrent, pages, loadPage]);

  const animating = useRef(false);
  const slideTo = useCallback(
    (target: number) =>
      new Promise<void>((resolve) => {
        const track = trackRef.current;
        const direction = Math.sign(target - safeCurrent);
        if (!track || direction === 0 || lowFx() || Math.abs(target - safeCurrent) > 1) {
          setCurrent(target);
          resolve();
          return;
        }
        animating.current = true;
        const animation = track.animate(
          [{ transform: track.style.transform || 'translateX(0)' }, { transform: `translateX(${-direction * 100}%)` }],
          { duration: 240, easing: 'cubic-bezier(.2,.7,.2,1)', fill: 'forwards' },
        );
        animation.onfinish = () => {
          setCurrent(target);
          requestAnimationFrame(() => {
            track.style.transform = '';
            animation.cancel();
            animating.current = false;
            resolve();
          });
        };
      }),
    [safeCurrent],
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

  const paste = async (card: Card, tile: HTMLElement) => {
    if (flying !== null || !index) return;
    setError('');
    const target = index.pages.findIndex((p) => p.org_id === card.org_id && p.page_no === (card.is_mosaic_piece ? 0 : card.page_no));
    if (target < 0) return;
    const position = target + 1;
    const meta = index.pages[target];
    const from = tile.getBoundingClientRect();
    setFlying(card.id);
    try {
      await post(`/album/paste/${card.id}`);
      if (position !== safeCurrent) await slideTo(position);
      if (!detailsRef.current[pageKey(meta)]) await loadPage(meta);
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await flyToSlot(card, from);
      await Promise.all([loadPage(meta), loadIndex()]);
      setLastPasted(card.id);
      buzz(15);
      window.setTimeout(() => setLastPasted(null), 500);
    } catch (err) {
      setError((err as Error).message);
      await loadIndex().catch(() => {});
    } finally {
      setFlying(null);
    }
  };

  if (!index) {
    return <div className="screen">{error ? <Message>{error}</Message> : <div className="album-skeleton" />}</div>;
  }

  const meta = pages[safeCurrent];
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
      {!user?.verified ? <VerifyBanner /> : null}
      <div className="album-bar">
        <button className="round" aria-label="Página anterior" disabled={safeCurrent === 0} onClick={() => slideTo(safeCurrent - 1)}>‹</button>
        <div className="album-bar-text">
          <strong>{meta ? meta.org_name : 'Capa'}</strong>
          <small>{meta ? `${meta.kind === 'mosaic' ? 'Mosaico' : `Página ${meta.page_no}`} · ${meta.filled}/${meta.total}` : `${index.filled} de ${index.total} figurinhas`}</small>
        </div>
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

      <section className={`tray${index.tray.length ? '' : ' tray-empty'}`} aria-label="Bandeja de figurinhas para colar">
        {index.tray.length ? (
          <>
            <p className="tray-label"><b>{index.tray.length}</b> para colar · toque na figurinha</p>
            <div className="tray-row">
              {index.tray.map((card) => (
                <button
                  key={card.id}
                  className={`tray-tile${flying === card.id ? ' taking-off' : ''}`}
                  aria-label={`Colar ${card.name}`}
                  disabled={!user?.verified}
                  onClick={(event) => paste(card, event.currentTarget)}
                >
                  <StickerArt card={card} />
                </button>
              ))}
            </div>
          </>
        ) : (
          <p className="tray-label">Bandeja vazia. Abra um pacote para ganhar figurinhas.</p>
        )}
      </section>

      {open ? <BackSheet card={open} onClose={closeBack} /> : null}
    </div>
  );
}

/** A figurinha sai da bandeja e voa até o espaço. Só usa transform, para manter 60 fps. */
function flyToSlot(card: Card, from: DOMRect): Promise<void> {
  const slot = document.querySelector<HTMLElement>(`.album-panel [data-slot="${card.id}"]`);
  if (!slot || lowFx() || !card.image_url) return Promise.resolve();
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
    { duration: 560, easing: 'cubic-bezier(.3,.6,.3,1)' },
  );
  return animation.finished.then(
    () => clone.remove(),
    () => clone.remove(),
  );
}
