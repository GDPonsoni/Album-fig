import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';

import type { CropState } from '../api';

export interface CropSpec {
  width: number; // tamanho de saída, em pixels: a imagem já sai reduzida do aparelho
  height: number;
  type: 'image/png' | 'image/jpeg';
  label: string;
}

const round = (value: number) => Math.round(Math.max(-2, Math.min(2, value)) * 1000) / 1000;
const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a);
const ratioText = (w: number, h: number) => {
  const d = gcd(w, h);
  return d > 0 && w / d <= 40 && h / d <= 40 ? `${w / d}:${h / d}` : `${(w / h).toFixed(2)}:1`;
};
const size = (bytes: number) => (bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`);

/** Recorte no cliente com proporção fixa: arrasta para posicionar, aproxima, gira, espelha e mostra o que vai sair. */
export function ImageCropper({ file, spec, initial, onDone, onCancel }: { file: File; spec: CropSpec; initial?: CropState | null; onDone: (blob: Blob, state: CropState) => void; onCancel: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const ratio = spec.width / spec.height;
  const viewW = Math.round(Math.min(300, ratio >= 1 ? 300 : 300 * ratio));
  const viewH = Math.round(viewW / ratio);
  const [zoom, setZoom] = useState(initial?.zoom ?? 1);
  const [offset, setOffset] = useState({ x: (initial?.x ?? 0) * viewW, y: (initial?.y ?? 0) * viewH });
  const [turns, setTurns] = useState(initial?.turns ?? 0); // giros de 90° no sentido horário
  const [flip, setFlip] = useState(initial?.flip ?? false);
  const [fit, setFit] = useState<'fill' | 'whole'>(initial?.fit ?? 'fill'); // preencher o quadro ou mostrar a imagem inteira
  const [error, setError] = useState('');
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  useEffect(() => {
    // Data URL e não blob:, porque a CSP do app só permite imagens 'self', data: e https.
    let alive = true;
    const reader = new FileReader();
    reader.onload = () => {
      const element = new Image();
      element.onload = () => alive && setImage(element);
      element.onerror = () => alive && setError('Não foi possível abrir esta imagem. Use PNG, JPEG ou WebP.');
      element.src = String(reader.result);
    };
    reader.onerror = () => alive && setError('Não foi possível ler este arquivo.');
    reader.readAsDataURL(file);
    return () => {
      alive = false;
    };
  }, [file]);

  // A imagem já girada/espelhada, pronta para desenhar. Girar 90° troca largura e altura.
  const source = useMemo(() => {
    if (!image) return null;
    const swap = turns % 2 === 1;
    const out = document.createElement('canvas');
    out.width = swap ? image.naturalHeight : image.naturalWidth;
    out.height = swap ? image.naturalWidth : image.naturalHeight;
    const context = out.getContext('2d');
    if (!context) return null;
    context.translate(out.width / 2, out.height / 2);
    context.rotate((turns * Math.PI) / 2);
    if (flip) context.scale(-1, 1);
    context.drawImage(image, -image.naturalWidth / 2, -image.naturalHeight / 2);
    return out;
  }, [image, turns, flip]);

  // Preencher: a imagem cobre o quadro. Inteira: cabe toda dentro dele (sobram margens). O deslocamento fica dentro das bordas.
  const geometry = useCallback(
    (frameW: number, frameH: number) => {
      if (!source) return null;
      const base = fit === 'fill' ? Math.max(frameW / source.width, frameH / source.height) : Math.min(frameW / source.width, frameH / source.height);
      const scale = base * zoom;
      const w = source.width * scale;
      const h = source.height * scale;
      const maxX = Math.max(0, (w - frameW) / 2);
      const maxY = Math.max(0, (h - frameH) / 2);
      const x = Math.min(maxX, Math.max(-maxX, offset.x * (frameW / viewW)));
      const y = Math.min(maxY, Math.max(-maxY, offset.y * (frameH / viewH)));
      return { left: (frameW - w) / 2 + x, top: (frameH - h) / 2 + y, w, h, scale };
    },
    [source, offset, viewH, viewW, zoom, fit],
  );

  const paint = (context: CanvasRenderingContext2D, w: number, h: number, box: { left: number; top: number; w: number; h: number }) => {
    context.clearRect(0, 0, w, h);
    if (spec.type === 'image/jpeg') {
      context.fillStyle = '#fff';
      context.fillRect(0, 0, w, h);
    }
    context.drawImage(source!, box.left, box.top, box.w, box.h);
  };

  useEffect(() => {
    const context = canvas.current?.getContext('2d');
    const box = geometry(viewW, viewH);
    if (!context || !box || !source) return;
    paint(context, viewW, viewH, box);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry, source, viewH, viewW]);

  const reset = () => {
    setZoom(1);
    setOffset({ x: 0, y: 0 });
    setTurns(0);
    setFlip(false);
    setFit('fill');
  };
  const onDown = (event: ReactPointerEvent) => {
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y };
  };
  const onMove = (event: ReactPointerEvent) => {
    const state = drag.current;
    if (state) setOffset({ x: state.ox + event.clientX - state.x, y: state.oy + event.clientY - state.y });
  };
  const confirm = () => {
    const box = geometry(spec.width, spec.height);
    if (!box || !source) return;
    const output = document.createElement('canvas');
    output.width = spec.width;
    output.height = spec.height;
    const context = output.getContext('2d');
    if (!context) return;
    paint(context, spec.width, spec.height, box);
    const state: CropState = { zoom, x: round(offset.x / viewW), y: round(offset.y / viewH), turns, flip, fit };
    output.toBlob((blob) => (blob ? onDone(blob, state) : setError('Não foi possível recortar a imagem.')), spec.type, 0.9);
  };

  const finalBox = geometry(spec.width, spec.height);
  // Quanto a imagem é ampliada para chegar ao tamanho final: acima de 1 fica pixelada.
  const upscale = finalBox?.scale ?? 1;
  const lowRes = upscale > 1.25;

  return (
    <div className="overlay cropper" role="dialog" aria-label={`Recortar ${spec.label}`}>
      <div className="panel paper">
        <h2>Recortar {spec.label}</h2>
        {error ? <p className="message message-error" role="alert">{error}</p> : (
          <>
            <canvas
              ref={canvas}
              width={viewW}
              height={viewH}
              className="cropper-canvas"
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={() => { drag.current = null; }}
              onPointerCancel={() => { drag.current = null; }}
            />
            <div className="cropper-tools" role="group" aria-label="Ajustes da imagem">
              <button type="button" className="btn" title="Girar para a esquerda" aria-label="Girar para a esquerda" onClick={() => setTurns((t) => (t + 3) % 4)}>⟲</button>
              <button type="button" className="btn" title="Girar para a direita" aria-label="Girar para a direita" onClick={() => setTurns((t) => (t + 1) % 4)}>⟳</button>
              <button type="button" className={`btn${flip ? ' on' : ''}`} title="Espelhar" aria-pressed={flip} onClick={() => setFlip((f) => !f)}>⇋</button>
              <button type="button" className="btn" title="Voltar ao início" onClick={reset}>Zerar</button>
            </div>
            <div className="segmented" role="group" aria-label="Como encaixar">
              <button type="button" className={fit === 'fill' ? 'active' : ''} onClick={() => { setFit('fill'); setZoom(1); setOffset({ x: 0, y: 0 }); }}>Preencher</button>
              <button type="button" className={fit === 'whole' ? 'active' : ''} onClick={() => { setFit('whole'); setZoom(1); setOffset({ x: 0, y: 0 }); }}>Imagem inteira</button>
            </div>
            <label className="field"><span>Aproximar ({Math.round(zoom * 100)}%)</span>
              <span className="zoom-row">
                <button type="button" className="btn" aria-label="Afastar" onClick={() => setZoom((z) => Math.max(0.5, Math.round((z - 0.1) * 100) / 100))}>−</button>
                <input type="range" min={fit === 'whole' ? 0.5 : 1} max={4} step={0.01} value={zoom} onChange={(event) => setZoom(Number(event.target.value))} />
                <button type="button" className="btn" aria-label="Aproximar" onClick={() => setZoom((z) => Math.min(4, Math.round((z + 0.1) * 100) / 100))}>+</button>
              </span>
            </label>
            {source ? (
              <dl className="cropper-info">
                <dt>Original</dt><dd>{image!.naturalWidth}×{image!.naturalHeight} px · {ratioText(image!.naturalWidth, image!.naturalHeight)} · {size(file.size)}</dd>
                <dt>Vai sair</dt><dd>{spec.width}×{spec.height} px · {ratioText(spec.width, spec.height)}</dd>
              </dl>
            ) : null}
            {lowRes ? <p className="message message-error" role="alert">Imagem pequena para este recorte: pode ficar borrada. Use uma foto maior ou aproxime menos.</p> : null}
            <p className="muted">Arraste a imagem para posicionar.</p>
          </>
        )}
        <div className="admin-actions">
          <button type="button" className="btn btn-primary" disabled={!source || !!error} onClick={confirm}>Usar este recorte</button>
          <button type="button" className="btn" onClick={onCancel}>Cancelar</button>
        </div>
      </div>
    </div>
  );
}
