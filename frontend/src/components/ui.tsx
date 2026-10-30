import { useState, type ReactNode } from 'react';
import { post, type Card } from '../api';

export function Splash() {
  return (
    <div className="splash" role="status">
      <span className="splash-mark">Álbum</span>
    </div>
  );
}

export function Message({ kind = 'error', children }: { kind?: 'error' | 'ok'; children: ReactNode }) {
  if (!children) return null;
  return (
    <p className={`message message-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {children}
    </p>
  );
}

export function StickerArt({ card, shine }: { card: Card; shine?: boolean }) {
  return (
    <div className={`sticker rank-${card.rarity.rank}${shine ? ' shine' : ''}`} style={{ '--org': card.org_color } as React.CSSProperties}>
      {card.image_url ? <img src={card.image_url} alt={card.name} loading="lazy" draggable={false} /> : <div className="sticker-blank" />}
      <span className="sticker-no">{card.number}</span>
    </div>
  );
}

export function VerifyBanner() {
  const [note, setNote] = useState('');
  const resend = async () => {
    try {
      const result = await post('/auth/resend');
      setNote(result.message);
    } catch (error) {
      setNote((error as Error).message);
    }
  };
  return (
    <div className="verify-banner">
      <p>
        <strong>Confirme seu e-mail</strong> para abrir pacotes e trocar.
      </p>
      {note ? <small>{note}</small> : <button className="link" onClick={resend}>Reenviar link</button>}
    </div>
  );
}
