import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, post } from '../api';
import { CodeSheet } from '../components/CodeSheet';
import { Message } from '../components/ui';

type Row = Record<string, any>;
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');

/** Área do promotor: gera lotes de códigos de pacote extra, com QR para imprimir. */
export default function PromoPage() {
  const [data, setData] = useState<Row | null>(null);
  const [form, setForm] = useState({ pack_type_id: 0, count: 10, label: '' });
  const [sheet, setSheet] = useState<{ code: string; label: string }[] | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    api<Row>('/promo/overview')
      .then((d) => {
        setData(d);
        setForm((f) => ({ ...f, pack_type_id: f.pack_type_id || d.pack_types[0]?.id || 0 }));
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    try {
      const created = await post<{ codes: string[] }>('/promo/codes', form);
      setSheet(created.codes.map((code) => ({ code, label: form.label })));
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  if (sheet) return <div className="screen"><CodeSheet codes={sheet} onClose={() => setSheet(null)} /></div>;
  if (!data) return <div className="screen"><h1 className="screen-title">Promotor</h1><Message>{error}</Message><Link className="btn" to="/conta">Voltar</Link></div>;
  const open = (data.codes as Row[]).filter((c) => !c.redeemed_by).map((c) => ({ code: c.code as string, label: (c.label as string) || '' }));
  return (
    <div className="screen">
      <h1 className="screen-title">Códigos de pacote</h1>
      <section className="panel paper">
        <h2>Gerar lote</h2>
        {data.album ? (
          <p className="muted">Álbum {data.album.title}. {data.unlimited ? 'Superadmin: sem limite de cota.' : <>Você ainda pode gerar <b>{data.left}</b> de {data.limit} códigos nas próximas 24 horas.</>}</p>
        ) : (
          <p className="muted">Não há álbum em andamento.</p>
        )}
        <form className="admin-form" onSubmit={create}>
          <label className="field"><span>Tipo de pacote</span>
            <select value={form.pack_type_id} onChange={(e) => setForm({ ...form, pack_type_id: Number(e.target.value) })}>
              {(data.pack_types as Row[]).map((p) => <option key={p.id} value={p.id}>{p.name} ({p.stickers_per_pack} figurinhas)</option>)}
            </select>
          </label>
          <label className="field"><span>Onde vai distribuir (rótulo)</span><input maxLength={80} value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
          <label className="field"><span>Quantidade</span><input type="number" min={1} max={Math.max(1, Math.min(200, data.left))} value={form.count} onChange={(e) => setForm({ ...form, count: Number(e.target.value) })} /></label>
          <button className="btn btn-primary" disabled={!data.album || data.left < 1 || !form.pack_type_id}>Gerar e imprimir QR</button>
        </form>
        <Message>{error}</Message>
      </section>
      <section className="panel paper">
        <h2>Meus códigos</h2>
        {open.length ? <button className="btn" onClick={() => setSheet(open)}>Imprimir não resgatados com QR ({open.length})</button> : null}
        <ul className="admin-list">
          {(data.codes as Row[]).map((c) => (
            <li key={c.id}>
              <span><code>{c.code}</code><small className="album-dates">{c.pack_type}{c.label ? ` · ${c.label}` : ''} · criado {when(c.created_at)}</small></span>
              <small>{c.redeemed_by ? `resgatado por ${c.redeemed_by} em ${when(c.redeemed_at)}` : 'disponível'}</small>
            </li>
          ))}
        </ul>
        {(data.codes as Row[]).length ? null : <p className="muted">Nenhum código ainda.</p>}
      </section>
      <Link className="btn" to="/conta">Voltar</Link>
    </div>
  );
}
