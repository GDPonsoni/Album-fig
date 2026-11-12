import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type AlbumStats, type AlbumSummary, type MyAlbums } from '../api';
import { useOnActivate } from '../components/Layout';
import { Message } from '../components/ui';

const day = (iso: string) => new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' });

function formatLeft(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const days = Math.floor(total / 86400);
  const pad = (n: number) => String(n).padStart(2, '0');
  const clock = `${pad(Math.floor(total / 3600) % 24)}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}`;
  return days ? `${days} ${days === 1 ? 'dia' : 'dias'} e ${clock}` : clock;
}

/** Conta até o fim do álbum pelo relógio do servidor. */
function useTimeLeft(endsAt: string | undefined, serverNow: string | undefined, onEnd: () => void) {
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (!endsAt || !serverNow) {
      setLeft(null);
      return;
    }
    const target = Date.parse(endsAt) - (Date.parse(serverNow) - Date.now());
    let fired = false;
    const tick = () => {
      const ms = target - Date.now();
      setLeft(ms);
      if (ms <= 0 && !fired) {
        fired = true;
        onEnd();
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endsAt, serverNow]);
  return left;
}

function Progress({ stats }: { stats: AlbumStats }) {
  const percent = stats.total_stickers ? Math.round((100 * stats.pasted) / stats.total_stickers) : 0;
  return (
    <div className="album-progress">
      <strong>{percent}%</strong>
      <span>{stats.pasted} de {stats.total_stickers} coladas</span>
      <div className="bar"><i style={{ width: `${percent}%` }} /></div>
    </div>
  );
}

function Stats({ stats }: { stats: AlbumStats }) {
  return (
    <dl className="album-stats">
      <div><dt>Recebidas</dt><dd>{stats.received_total}</dd></div>
      <div><dt>Repetidas</dt><dd>{stats.duplicates}</dd></div>
      <div><dt>Trocas</dt><dd>{stats.trades_done}</dd></div>
    </dl>
  );
}

function Cover({ album }: { album: AlbumSummary }) {
  return album.cover_url ? <img className="album-cover" src={album.cover_url} alt="" loading="lazy" /> : <div className="album-cover album-cover-blank" aria-hidden="true">{album.title.slice(0, 1)}</div>;
}

export function MyAlbumsSection() {
  const [data, setData] = useState<MyAlbums | null>(null);
  const [error, setError] = useState('');
  const load = () => api<MyAlbums>('/albums/mine').then(setData).catch((err) => setError(err.message));
  useEffect(() => {
    load();
  }, []);
  useOnActivate(() => {
    load().catch(() => {});
  });
  const left = useTimeLeft(data?.active?.ends_at, data?.server_now, load);

  return (
    <section className="my-albums" aria-label="Meus álbuns">
      <h2 className="extras-title">Meus álbuns</h2>
      <Message>{error}</Message>
      {data ? (
        <>
          {data.active ? (
            <section className="panel paper album-card album-card-active" aria-label="Álbum em andamento">
              <div className="album-card-head">
                <Cover album={data.active} />
                <div>
                  <p className="album-kicker">Em andamento</p>
                  <h2>{data.active.title}</h2>
                  <p className="muted">Até {day(data.active.ends_at)}</p>
                </div>
              </div>
              <p className="album-timer">
                <small>Termina em</small>
                <strong role="timer">{left === null ? '…' : formatLeft(left)}</strong>
              </p>
              <Progress stats={data.active.stats} />
              <Stats stats={data.active.stats} />
              <Link className="btn btn-primary" to="/">Abrir álbum</Link>
            </section>
          ) : (
            <section className="panel paper empty-album">
              <h2>Nenhum álbum em andamento</h2>
              <p className="muted">
                {data.upcoming
                  ? `O próximo, ${data.upcoming.title}, começa em ${day(data.upcoming.starts_at)}.`
                  : 'Quando o próximo álbum começar, ele aparece aqui.'}
              </p>
            </section>
          )}

          <h3 className="extras-title">Encerrados</h3>
          {data.ended.length ? (
            <ul className="album-list">
              {data.ended.map((album) => (
                <li key={album.id} className="panel paper album-card">
                  <div className="album-card-head">
                    <Cover album={album} />
                    <div>
                      <h3>{album.title}</h3>
                      <p className="muted">{day(album.starts_at)} a {day(album.ends_at)}</p>
                      {album.stats.completed_at ? <p className="album-complete">Álbum completo!</p> : null}
                    </div>
                  </div>
                  <Progress stats={album.stats} />
                  <Stats stats={album.stats} />
                  <Link className="btn" to={`/albuns/${album.id}`}>Ver álbum</Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="album-none">Os álbuns que terminarem ficam guardados aqui, do jeito que você deixou.</p>
          )}
        </>
      ) : null}
    </section>
  );
}
