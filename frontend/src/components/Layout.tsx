import { createContext, useContext, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';

const icons = {
  album: 'M4 4.5A1.5 1.5 0 0 1 5.5 3H19a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H6a2 2 0 0 1-2-2zM8 3v17',
  pack: 'M6 3h12l1 3-1 2v9l1 2-1 2H6l-1-2 1-2V8L5 6zM6 8h12M6 17h12',
  trade: 'M4 8h13l-3.5-3.5M20 16H7l3.5 3.5',
  me: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20.5a7.5 7.5 0 0 1 15 0',
};

function Tab({ to, icon, label, badge }: { to: string; icon: keyof typeof icons; label: string; badge?: number }) {
  return (
    <NavLink to={to} end={to === '/'} className="tab">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d={icons[icon]} />
      </svg>
      <span>{label}</span>
      {badge ? <b className="tab-badge">{badge}</b> : null}
    </NavLink>
  );
}

const TAB_ORDER = ['/', '/pacotes', '/trocar', '/conta'];
/** Gestos que começam aqui não viram troca de aba: o álbum e a bandeja têm os seus. */
const NO_SWIPE = '.album-stage, .tray-row, input, textarea, select, canvas, [data-noswipe]';

const ShellContext = createContext<{ setStock: (n: number) => void }>({ setStock: () => {} });
export const useShell = () => useContext(ShellContext);

export function Layout({ panes = {} }: { panes?: Record<string, ReactNode> }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [stock, setStock] = useState(0);
  const [dragging, setDragging] = useState(false);
  // Segurou o toque por um instante: já monta as vizinhas, para elas estarem prontas quando o arrasto começar.
  const [armed, setArmed] = useState(false);
  const armTimer = useRef(0);
  const track = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ x: number; y: number; id: number; horizontal: boolean | null } | null>(null);
  const busy = useRef(false);
  const swiped = useRef(false);

  const index = TAB_ORDER.indexOf(location.pathname);
  const prev = index > 0 ? TAB_ORDER[index - 1] : null;
  const next = index >= 0 && index < TAB_ORDER.length - 1 ? TAB_ORDER[index + 1] : null;

  // Troca por toque na barra desliza a página nova para dentro; troca por gesto já veio deslizando.
  const entry = useRef({ path: location.pathname, index, cls: '' });
  if (entry.current.path !== location.pathname) {
    const dir = index >= 0 && entry.current.index >= 0 ? Math.sign(index - entry.current.index) : 0;
    entry.current = { path: location.pathname, index, cls: swiped.current || !dir ? '' : dir > 0 ? ' enter-next' : ' enter-prev' };
    swiped.current = false;
  }

  useEffect(() => {
    api('/packs')
      .then((packs) => setStock(packs.stock.length))
      .catch(() => {});
  }, [location.pathname]);

  const reset = () => {
    if (track.current) {
      track.current.style.transition = '';
      track.current.style.transform = '';
    }
    busy.current = false;
    window.clearTimeout(armTimer.current);
    setDragging(false);
    setArmed(false);
  };
  useEffect(reset, [location.pathname]);

  const onDown = (event: ReactPointerEvent) => {
    if (index < 0 || busy.current || event.pointerType === 'mouse') return;
    if ((event.target as HTMLElement).closest(NO_SWIPE)) return;
    gesture.current = { x: event.clientX, y: event.clientY, id: event.pointerId, horizontal: null };
    window.clearTimeout(armTimer.current);
    armTimer.current = window.setTimeout(() => setArmed(true), 70);
  };
  const onMove = (event: ReactPointerEvent) => {
    const g = gesture.current;
    if (!g || g.id !== event.pointerId || !track.current) return;
    const dx = event.clientX - g.x;
    const dy = event.clientY - g.y;
    if (g.horizontal === null && Math.hypot(dx, dy) > 10) {
      g.horizontal = Math.abs(dx) > Math.abs(dy) * 1.2;
      if (g.horizontal) {
        (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
        setDragging(true);
      } else {
        gesture.current = null;
      }
    }
    if (g.horizontal) {
      const blocked = (dx > 0 && !prev) || (dx < 0 && !next);
      track.current.style.transition = 'none';
      track.current.style.transform = `translateX(${blocked ? dx / 5 : dx}px)`;
    }
  };
  const onEnd = (event: ReactPointerEvent) => {
    const g = gesture.current;
    gesture.current = null;
    const el = track.current;
    if (!g?.horizontal || !el) {
      reset();
      return;
    }
    const width = el.clientWidth;
    const dx = event.clientX - g.x;
    const target = dx < 0 ? next : prev;
    const commit = event.type !== 'pointercancel' && target !== null && Math.abs(dx) > width / 2;
    busy.current = true;
    el.style.transition = 'transform 0.22s ease-out';
    el.style.transform = commit ? `translateX(${dx < 0 ? -width : width}px)` : 'translateX(0)';
    window.setTimeout(() => {
      if (commit && target) {
        swiped.current = true;
        navigate(target);
      }
      else reset();
    }, 230);
  };

  return (
    <ShellContext.Provider value={{ setStock }}>
      <div className="shell">
        <main className="shell-main" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onEnd} onPointerCancel={onEnd}>
          <div className={`pager${dragging ? ' moving' : ''}`} ref={track}>
            {(dragging || armed) && prev ? <div className="pane pane-prev" aria-hidden="true">{panes[prev]}</div> : null}
            <div className={`pane${entry.current.cls}`} key={location.pathname}>
              <Outlet />
            </div>
            {(dragging || armed) && next ? <div className="pane pane-next" aria-hidden="true">{panes[next]}</div> : null}
          </div>
        </main>
        <nav className="tabs" aria-label="Principal">
          <Tab to="/" icon="album" label="Álbum" />
          <Tab to="/pacotes" icon="pack" label="Pacotes" badge={stock} />
          <Tab to="/trocar" icon="trade" label="Trocar" />
          <Tab to="/conta" icon="me" label="Conta" />
        </nav>
      </div>
    </ShellContext.Provider>
  );
}
