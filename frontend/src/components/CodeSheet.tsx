import QRCode from 'qrcode';
import { useEffect, useState } from 'react';

/** Folha imprimível: um cartão por código, com o QR do link de resgate e o texto ao lado. */
export function CodeSheet({ codes, onClose }: { codes: { code: string; label: string }[]; onClose: () => void }) {
  const [images, setImages] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    Promise.all(
      codes.map(async ({ code }) => [code, await QRCode.toDataURL(`${window.location.origin}/r/${code}`, { width: 240, margin: 1 })] as const),
    ).then((pairs) => alive && setImages(Object.fromEntries(pairs)));
    return () => {
      alive = false;
    };
  }, [codes]);
  useEffect(() => {
    document.body.classList.add('printing-codes');
    return () => document.body.classList.remove('printing-codes');
  }, []);
  const ready = Object.keys(images).length === codes.length;
  return (
    <div className="code-sheet">
      <div className="code-sheet-bar">
        <strong>{codes.length} {codes.length === 1 ? 'código' : 'códigos'} para imprimir</strong>
        <button className="btn btn-primary" disabled={!ready} onClick={() => window.print()}>Imprimir</button>
        <button className="btn" onClick={onClose}>Fechar</button>
      </div>
      <ul className="code-cards">
        {codes.map(({ code, label }) => (
          <li key={code}>
            {images[code] ? <img src={images[code]} alt={`QR do código ${code}`} /> : <i />}
            <div>
              <small>Aponte a câmera ou digite no app</small>
              <code>{code}</code>
              {label ? <span>{label}</span> : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
