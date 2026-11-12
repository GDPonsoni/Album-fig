// Renderização de uma página do álbum. É o MESMO componente no jogo, na prévia do editor e no
// painel de revisão: o que o superadmin aprova é exatamente o que o jogador vai ver.
import type { PageDetail, Slot } from '../api';
import { StickerArt } from './ui';

const pageClass = (detail: PageDetail) => `album-page paper${detail.orientation === 'landscape' ? ' landscape' : ''}`;

function pageStyle(detail: PageDetail): React.CSSProperties {
  const style = { '--org': detail.org.color } as React.CSSProperties;
  // O fundo entra por cima da textura do papel; a URL vem do servidor (ativo publicado), nunca de texto livre.
  if (detail.page?.background_url) {
    style.backgroundImage = `url(${JSON.stringify(detail.page.background_url)})`;
    style.backgroundSize = 'cover';
    style.backgroundPosition = 'center';
  }
  return style;
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

/** Título longo encolhe em vez de estourar o cabeçalho (que tem altura fixa para não cobrir as figurinhas). */
const titleScale = (title: string) => (title.length > 28 ? 0.6 : title.length > 16 ? 0.78 : 1);

export function StickerPage({ detail, onOpen, lastPasted, overlay }: { detail: PageDetail; onOpen: (slot: Slot) => void; lastPasted: number | null; overlay?: React.ReactNode }) {
  const title = detail.page?.title || detail.org.name;
  return (
    <article className={pageClass(detail)} style={{ ...pageStyle(detail), '--ts': titleScale(title) } as React.CSSProperties}>
      <header className="page-head">
        {detail.org.logo_url ? <img src={detail.org.logo_url} alt="" /> : null}
        <div>
          <h2>{title}</h2>
          {detail.page?.text ? <p>{detail.page.text}</p> : detail.page_no === 1 ? <p>{detail.org.description}</p> : null}
        </div>
      </header>
      {detail.slots.map((slot) => (
        <button
          key={slot.sticker_id}
          className={`slot${lastPasted === slot.sticker_id ? ' just-pasted' : ''}`}
          data-slot={slot.sticker_id}
          disabled={!slot.filled}
          aria-label={slot.filled ? `Ver verso de ${slot.card!.name}` : `Espaço vazio ${slot.number}`}
          style={{ left: `${slot.layout.x}%`, top: `${slot.layout.y}%`, width: `${slot.layout.w}%`, height: `${slot.layout.h}%`, ...(slot.layout.rot ? { transform: `rotate(${slot.layout.rot}deg)` } : null) }}
          onClick={() => onOpen(slot)}
        >
          {slot.filled ? <StickerArt card={slot.card!} /> : <EmptySlot slot={slot} />}
        </button>
      ))}
      {overlay}
    </article>
  );
}

/** Largura do mosaico em % da página: cada peça tem o tamanho de uma figurinha pequena (P) normal;
 *  se a grade não couber na página, encolhe tudo na mesma proporção. */
function mosaicWidth(cols: number, rows: number, landscape: boolean): number {
  const piece = landscape ? 21 * 0.72 : 21; // % da largura da página, como `slotFor('P')` do editor
  const pageH = landscape ? 100 / 1.38 : 138; // altura da página em % da largura
  const naturalW = cols * piece;
  const naturalH = rows * piece * (4 / 3);
  return Math.round(naturalW * Math.min(1, 92 / naturalW, (pageH * 0.8) / naturalH) * 10) / 10;
}

export function MosaicPage({ detail, onOpen, lastPasted, lines, selected }: { detail: PageDetail; onOpen: (slot: Slot) => void; lastPasted: number | null; lines?: boolean; selected?: number | null }) {
  const { org } = detail;
  const filled = detail.slots.filter((s) => s.filled).length;
  return (
    <article className={pageClass(detail)} style={{ '--org': org.color } as React.CSSProperties}>
      <header className="page-head">
        <div>
          <h2>Mosaico</h2>
          <p>{org.name}: {filled} de {detail.slots.length} peças</p>
        </div>
      </header>
      <div
        className={`mosaic${lines ? ' lines' : ''}`}
        style={{
          gridTemplateColumns: `repeat(${org.mosaic_cols}, 1fr)`, gridTemplateRows: `repeat(${org.mosaic_rows}, 1fr)`,
          aspectRatio: `${3 * org.mosaic_cols} / ${4 * org.mosaic_rows}`, width: `${mosaicWidth(org.mosaic_cols, org.mosaic_rows, detail.orientation === 'landscape')}%`,
        }}
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
              className={`mosaic-piece${slot.mosaic_index === selected ? ' sel' : ''}${slot.filled ? ' filled' : ''}${lastPasted === slot.sticker_id ? ' just-pasted' : ''}`}
              style={
                slot.filled && !(slot.card?.image_url && slot.sticker_id > 0)
                  ? { backgroundImage: `url(${org.mosaic_image_url})`, backgroundSize: `${org.mosaic_cols * 100}% ${org.mosaic_rows * 100}%`, backgroundPosition: `${x}% ${y}%` }
                  : undefined
              }
              onClick={() => onOpen(slot)}
            >
              {slot.filled ? (slot.card?.image_url && slot.sticker_id > 0 ? <img src={slot.card.image_url} alt="" draggable={false} /> : null) : <b>{slot.number}</b>}
            </button>
          );
        })}
      </div>
    </article>
  );
}
