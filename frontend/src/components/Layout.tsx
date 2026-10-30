import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
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

export function Layout() {
  const location = useLocation();
  const [stock, setStock] = useState(0);

  useEffect(() => {
    api('/packs')
      .then((packs) => setStock(packs.stock.length))
      .catch(() => {});
  }, [location.pathname]);

  return (
    <div className="shell">
      <main className="shell-main">
        <Outlet context={{ setStock }} />
      </main>
      <nav className="tabs" aria-label="Principal">
        <Tab to="/" icon="album" label="Álbum" />
        <Tab to="/pacotes" icon="pack" label="Pacotes" badge={stock} />
        <Tab to="/trocar" icon="trade" label="Trocar" />
        <Tab to="/conta" icon="me" label="Conta" />
      </nav>
    </div>
  );
}
