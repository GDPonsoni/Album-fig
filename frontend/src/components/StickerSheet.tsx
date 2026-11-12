import { useEffect, useState } from 'react';
import { api, type Card } from '../api';
import { StickerArt } from './ui';

/** Figurinha ampliada. O verso só existe para quem já colou. */
type Back = { back_text: string; back_links: { label: string; url: string }[] };

/** `preview` traz o verso pronto (prévia do editor): não consulta o servidor. */
export function StickerSheet({ card, onClose, pasted = true, preview }: { card: Card; onClose: () => void; pasted?: boolean; preview?: Back }) {
  const [back, setBack] = useState<Back | null>(preview ?? null);
  const [flipped, setFlipped] = useState(false);
  useEffect(() => {
    if (pasted && !preview) api(`/stickers/${card.id}/back`).then(setBack).catch(onClose);
  }, [card.id, onClose, pasted, preview]);
  const flip = () => {
    if (pasted) setFlipped((value) => !value);
  };
  return (
    <div className="overlay" onClick={onClose}>
      <div
        className={`flip${flipped ? ' flipped' : ''}${pasted ? '' : ' flip-locked'}`}
        onClick={(event) => {
          event.stopPropagation();
          flip();
        }}
      >
        <div className="flip-face"><StickerArt card={card} /></div>
        <div className="flip-face flip-back paper">
          <p className="flip-org" style={{ color: card.org_color }}>{card.org_name}</p>
          <h3>{card.name}</h3>
          <p className="flip-rarity" style={{ color: card.rarity.color }}>#{card.number} · {card.rarity.name} · {card.tag}</p>
          <p>{back?.back_text}</p>
          <div className="flip-links">
            {back?.back_links.map((link) => (
              <a key={link.url} href={link.url} target="_blank" rel="noopener noreferrer" onClick={(event) => event.stopPropagation()}>{link.label}</a>
            ))}
          </div>
        </div>
      </div>
      {pasted ? null : <p className="flip-hint">Cole esta figurinha no álbum para ver o verso.</p>}
      <div className="flip-actions" onClick={(event) => event.stopPropagation()}>
        {pasted ? <button className="btn btn-primary" onClick={flip}>{flipped ? 'Ver frente' : 'Virar'}</button> : null}
        <button className="btn btn-ghost" onClick={onClose}>Fechar</button>
      </div>
    </div>
  );
}
