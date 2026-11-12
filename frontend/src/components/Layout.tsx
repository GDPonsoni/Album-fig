import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { NavLink, Outlet, UNSAFE_LocationContext, useLocation, useNavigate, useNavigationType, type Location } from 'react-router-dom';
import { api } from '../api';

const icons = {
  album: 'M4 4.5A1.5 1.5 0 0 1 5.5 3H19a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H6a2 2 0 0 1-2-2zM8 3v17',
  pack: 'M6 3h12l1 3-1 2v9l1 2-1 2H6l-1-2 1-2V8L5 6zM6 8h12M6 17h12',
  trade: 'M4 8h13l-3.5-3.5M20 16H7l3.5 3.5',
  me: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20.5a7.5 7.5 0 0 1 15 0',
};

function Tab({ to, icon, label, badge }: { to: string; icon: keyof typeof icons; label: string; badge?: number }) {
  return (
    <NavLink to={to} end={to.startsWith('/?') || to === '/'} className="tab">
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

/** Falso enquanto a aba está escondida (mas viva); vira verdadeiro quando ela volta à tela. */
const TabActive = createContext(true);

export const useTabActive = () => useContext(TabActive);

/** Roda `refresh` quando a aba volta à tela. As abas ficam montadas, então é aqui que o dado velho é renovado. */
export function useOnActivate(refresh: () => void) {
  const active = useContext(TabActive);
  const latest = useRef(refresh);
  latest.current = refresh;
  const was = useRef(active);
  useEffect(() => {
    if (active && !was.current) latest.current();
    was.current = active;
  }, [active]);
}

/** Abas já visitadas ficam montadas: trocar de aba não refaz a tela nem as requisições. */
function TabPane({ path, offset, visible, active, cls, children }: { path: string; offset: number; visible: boolean; active: boolean; cls: string; children: ReactNode }) {
  const live = useLocation();
  const navigationType = useNavigationType();
  const frozen = useRef<Location>(live);
  if (active) frozen.current = live;
  // A aba escondida mantém o endereço de quando saiu (o álbum guarda a página na URL).
  const value = { location: frozen.current, navigationType };
  return (
    <div
      className={`pane${active ? cls : ''}`}
      style={{ left: `${offset * 100}%`, right: `${-offset * 100}%`, visibility: visible ? 'visible' : 'hidden' }}
      inert={!active}
      aria-hidden={!active}
      data-pane={path}
    >
      <TabActive.Provider value={active}>
        <UNSAFE_LocationContext.Provider value={value}>{children}</UNSAFE_LocationContext.Provider>
      </TabActive.Provider>
    </div>
  );
}

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

  // A aba Álbum volta à página em que o jogador parou.
  const albumLink = useRef('/');
  if (location.pathname === '/') albumLink.current = `/${location.search}`;

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

  // Selo de pacotes: a aba Pacotes já mantém o número em dia; nas outras, confere de vez em quando.
  const stockAt = useRef(0);
  useEffect(() => {
    if (location.pathname === '/pacotes' || Date.now() - stockAt.current < 30000) return;
    stockAt.current = Date.now();
    api('/packs')
      .then((packs) => setStock(packs.stock.length))
      .catch(() => {});
  }, [location.pathname]);

  const [visited, setVisited] = useState<string[]>(() => (index >= 0 ? [location.pathname] : []));
  const wanted = [...(index >= 0 ? [location.pathname] : []), ...(armed || dragging ? [prev, next] : [])].filter((p): p is string => !!p);
  const missing = wanted.filter((p) => !visited.includes(p));
  if (missing.length) setVisited([...visited, ...missing]);

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
  useLayoutEffect(reset, [location.pathname]);

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
            {TAB_ORDER.filter((path) => visited.includes(path)).map((path) => {
              const position = TAB_ORDER.indexOf(path);
              const active = position === index;
              const adjacent = index >= 0 && (dragging || armed) && Math.abs(position - index) === 1;
              return (
                <TabPane key={path} path={path} offset={index < 0 ? position + 1 : position - index} visible={active || adjacent} active={active} cls={entry.current.cls}>
                  {panes[path]}
                </TabPane>
              );
            })}
            {index < 0 ? (
              <div className="pane">
                <Outlet />
              </div>
            ) : null}
          </div>
        </main>
        <nav className="tabs" aria-label="Principal">
          <Tab to={albumLink.current} icon="album" label="Álbum" />
          <Tab to="/pacotes" icon="pack" label="Pacotes" badge={stock} />
          <Tab to="/trocar" icon="trade" label="Trocar" />
          <Tab to="/conta" icon="me" label="Conta" />
        </nav>
      </div>
    </ShellContext.Provider>
  );
}
