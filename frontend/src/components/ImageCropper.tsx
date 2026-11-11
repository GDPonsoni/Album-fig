import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';

export interface CropSpec {
  width: number; // tamanho de saída, em pixels: a imagem já sai reduzida do aparelho
  height: number;
  type: 'image/png' | 'image/jpeg';
  label: string;
}

/** Recorte no cliente com proporção fixa: arrasta para posicionar, desliza para aproximar. */
export function ImageCropper({ file, spec, onDone, onCancel }: { file: File; spec: CropSpec; onDone: (blob: Blob) => void; onCancel: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [error, setError] = useState('');
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const ratio = spec.width / spec.height;
  const viewW = Math.round(Math.min(300, ratio >= 1 ? 300 : 300 * ratio));
  const viewH = Math.round(viewW / ratio);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    const element = new Image();
    element.onload = () => setImage(element);
    element.onerror = () => setError('Não foi possível abrir esta imagem. Use PNG, JPEG ou WebP.');
    element.src = url;
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // A imagem sempre cobre o quadro: a escala mínima é a de "cobrir", e o deslocamento é limitado às bordas.
  const geometry = useCallback(
    (frameW: number, frameH: number) => {
      if (!image) return null;
      const scale = Math.max(frameW / image.naturalWidth, frameH / image.naturalHeight) * zoom;
      const w = image.naturalWidth * scale;
      const h = image.naturalHeight * scale;
      const maxX = (w - frameW) / 2;
      const maxY = (h - frameH) / 2;
      const x = Math.min(maxX, Math.max(-maxX, offset.x * (frameW / viewW)));
      const y = Math.min(maxY, Math.max(-maxY, offset.y * (frameH / viewH)));
      return { left: (frameW - w) / 2 + x, top: (frameH - h) / 2 + y, w, h };
    },
    [image, offset, viewH, viewW, zoom],
  );

  useEffect(() => {
    const context = canvas.current?.getContext('2d');
    const box = geometry(viewW, viewH);
    if (!context || !box || !image) return;
    context.clearRect(0, 0, viewW, viewH);
    context.drawImage(image, box.left, box.top, box.w, box.h);
  }, [geometry, image, viewH, viewW]);

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
    if (!box || !image) return;
    const output = document.createElement('canvas');
    output.width = spec.width;
    output.height = spec.height;
    const context = output.getContext('2d');
    if (!context) return;
    if (spec.type === 'image/jpeg') {
      context.fillStyle = '#fff';
      context.fillRect(0, 0, spec.width, spec.height);
    }
    context.drawImage(image, box.left, box.top, box.w, box.h);
    output.toBlob((blob) => (blob ? onDone(blob) : setError('Não foi possível recortar a imagem.')), spec.type, 0.9);
  };

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
            <label className="field"><span>Aproximar</span>
              <input type="range" min={1} max={4} step={0.01} value={zoom} onChange={(event) => setZoom(Number(event.target.value))} />
            </label>
            <p className="muted">Arraste a imagem para posicionar.</p>
          </>
        )}
        <div className="admin-actions">
          <button type="button" className="btn btn-primary" disabled={!image || !!error} onClick={confirm}>Usar este recorte</button>
          <button type="button" className="btn" onClick={onCancel}>Cancelar</button>
        </div>
      </div>
    </div>
  );
}
