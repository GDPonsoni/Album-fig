import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Message } from '../components/ui';

type Row = Record<string, any>;
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('pt-BR') : '—');
const KINDS = { code: 'Código de pacote', pack: 'Pacote (número)', sticker: 'Figurinha (id)', user: 'Pessoa (e-mail)' } as const;
const EVENT: Record<string, string> = { drawn: 'saiu do pacote', traded: 'trocada', pasted: 'colada', legacy: 'anterior ao rastreio' };

function Origin({ pack }: { pack: Row }) {
  return (
    <p>
      <b>{pack.pack}</b> · origem: {pack.source}{pack.source_ref ? ` (${pack.source_ref})` : ''} · gerado por <b>{pack.generated_by ?? 'sistema'}</b>
      {pack.code ? <> · código <code>{pack.code.code}</code>{pack.code.label ? ` (${pack.code.label})` : ''} criado por <b>{pack.code.created_by ?? '—'}</b> em {when(pack.code.created_at)}</> : null}
      {' '}· dono <b>{pack.owner}</b> · recebido {when(pack.granted_at)} · aberto {when(pack.opened_at)}
    </p>
  );
}

function Copy({ copy }: { copy: Row }) {
  return (
    <details>
      <summary>#{copy.sticker.number} {copy.sticker.name} · exemplar {copy.copy_id} · hoje com <b>{copy.owner ?? '—'}</b></summary>
      {copy.origin ? <Origin pack={copy.origin} /> : <p className="muted">Sem origem registrada (exemplar anterior ao rastreio).</p>}
      <ol>
        {copy.events.map((e: Row, i: number) => (
          <li key={i}>{when(e.at)} · {EVENT[e.event] ?? e.event}{e.from ? ` de ${e.from}` : ''}{e.to ? ` para ${e.to}` : ''}{e.trade_id ? ` (troca ${e.trade_id})` : ''}</li>
        ))}
      </ol>
    </details>
  );
}

function PackView({ pack }: { pack: Row }) {
  return (
    <>
      <Origin pack={pack} />
      {(pack.items as Row[]).map((item) => (item.copy ? <Copy key={item.position} copy={item.copy} /> : <p key={item.position} className="muted">Posição {item.position + 1}: sem exemplar registrado.</p>))}
    </>
  );
}

/** Rastreio do superadmin: de um código ao pacote, do pacote a cada figurinha e de cada figurinha às trocas. */
export default function AdminTrace() {
  const [kind, setKind] = useState<keyof typeof KINDS>('code');
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<{ kind: keyof typeof KINDS; data: Row } | null>(null);
  const [error, setError] = useState('');
  const search = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setResult(null);
    try {
      const value = encodeURIComponent(query.trim());
      const data = await api<Row>(kind === 'user' ? `/admin/trace/user?email=${value}` : `/admin/trace/${kind}/${value}`);
      setResult({ kind, data });
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const data = result?.data;
  return (
    <div className="admin">
      <header className="admin-top">
        <h1>Rastreio</h1>
        <Link to="/admin">Painel admin</Link>
      </header>
      <form className="admin-form" onSubmit={search}>
        <label className="field"><span>Buscar</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as keyof typeof KINDS)}>
            {Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="field"><span>Valor</span><input required value={query} onChange={(e) => setQuery(e.target.value)} /></label>
        <button className="btn btn-primary">Rastrear</button>
      </form>
      <Message>{error}</Message>
      {data && result?.kind === 'code' ? (
        <section>
          <h2>Código <code>{data.code}</code></h2>
          <p>{data.pack_type}{data.label ? ` · ${data.label}` : ''} · criado por <b>{data.created_by ?? '—'}</b> em {when(data.created_at)} · resgatado por <b>{data.redeemed_by ?? 'ninguém'}</b> {when(data.redeemed_at)}</p>
          {data.pack ? <PackView pack={data.pack} /> : <p className="muted">Ainda não virou pacote.</p>}
        </section>
      ) : null}
      {data && result?.kind === 'pack' ? <section><h2>Pacote {data.grant_id}</h2><PackView pack={data as Row} /></section> : null}
      {data && result?.kind === 'sticker' ? (
        <section>
          <h2>#{data.sticker.number} {data.sticker.name}</h2>
          <h3>Edição e publicação</h3>
          {(data.revisions as Row[]).length ? (
            <ul>{(data.revisions as Row[]).map((r) => <li key={r.revision_id}>Revisão {r.number}: escrita por {(r.contributors as string[]).join(', ') || r.author || '—'}, aprovada por <b>{r.reviewed_by ?? '—'}</b>, publicada {when(r.published_at)}</li>)}</ul>
          ) : <p className="muted">Sem revisão publicada que a contenha (criada pelo admin ou pelo seed).</p>}
          <h3>Exemplares ({data.copy_total})</h3>
          {(data.copies as Row[]).map((c) => <Copy key={c.copy_id} copy={c} />)}
        </section>
      ) : null}
      {data && result?.kind === 'user' ? (
        <section>
          <h2>{data.user.nickname}</h2>
          <h3>Pacotes recebidos</h3>
          {(data.packs as Row[]).map((p) => <Origin key={p.grant_id} pack={p} />)}
          <h3>Pacotes que esta pessoa gerou</h3>
          {(data.packs_generated_by_user as Row[]).length ? (data.packs_generated_by_user as Row[]).map((p) => <Origin key={p.grant_id} pack={p} />) : <p className="muted">Nenhum.</p>}
        </section>
      ) : null}
    </div>
  );
}
