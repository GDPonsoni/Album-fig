import { useEffect, useRef, useState } from 'react';
import { parseTradeLink } from '../qr';

interface Detector {
  detect: (source: HTMLVideoElement) => Promise<{ rawValue: string }[]>;
}
type DetectorCtor = new (options: { formats: string[] }) => Detector;

function cameraError(error: unknown): string {
  const name = (error as { name?: string } | null)?.name;
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'A câmera foi bloqueada. Libere o acesso à câmera nas permissões do navegador ou peça o link da troca.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Nenhuma câmera encontrada neste aparelho. Peça o link da troca.';
  if (name === 'NotReadableError') return 'A câmera está em uso por outro app. Feche-o e tente de novo.';
  return 'Não foi possível abrir a câmera. Peça o link da troca.';
}

/** Lê o QR de uma troca com a câmera: BarcodeDetector quando existe, jsqr quando não. */
export function QrScanner({ onRead, onClose }: { onRead: (path: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const read = useRef(onRead);
  read.current = onRead;

  useEffect(() => {
    let alive = true;
    let stream: MediaStream | null = null;
    let timer = 0;

    const start = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setError('Este navegador não dá acesso à câmera. Peça o link da troca.');
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      } catch (err) {
        if (alive) setError(cameraError(err));
        return;
      }
      const element = video.current;
      if (!alive || !element) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      element.srcObject = stream;
      await element.play().catch(() => {});

      const Native = (window as unknown as { BarcodeDetector?: DetectorCtor }).BarcodeDetector;
      let detector: Detector | null = null;
      try {
        detector = Native ? new Native({ formats: ['qr_code'] }) : null;
      } catch {
        detector = null;
      }
      const fallback = detector ? null : (await import('../qrDecode')).decodeFrame;
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d', { willReadFrequently: true });

      const scan = async () => {
        if (!alive) return;
        let text: string | null = null;
        try {
          if (element.readyState >= 2 && element.videoWidth) {
            if (detector) {
              text = (await detector.detect(element))[0]?.rawValue ?? null;
            } else if (fallback && context) {
              // Quadro reduzido: decodificar em resolução cheia trava aparelho fraco.
              const scale = Math.min(1, 640 / Math.max(element.videoWidth, element.videoHeight));
              canvas.width = Math.round(element.videoWidth * scale);
              canvas.height = Math.round(element.videoHeight * scale);
              context.drawImage(element, 0, 0, canvas.width, canvas.height);
              text = fallback(context.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
            }
          }
        } catch {
          text = null;
        }
        if (!alive) return;
        if (text) {
          const path = parseTradeLink(text, window.location.origin);
          if (path) {
            read.current(path);
            return;
          }
          setNote('Este QR não é de uma troca deste álbum.');
        }
        timer = window.setTimeout(scan, 180);
      };
      scan();
    };
    start();

    return () => {
      alive = false;
      window.clearTimeout(timer);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  return (
    <div className="overlay scanner" role="dialog" aria-label="Ler QR de troca">
      {error ? (
        <p className="message message-error" role="alert">{error}</p>
      ) : (
        <>
          <div className="scanner-view">
            <video ref={video} playsInline muted aria-label="Imagem da câmera" />
            <i aria-hidden="true" />
          </div>
          <p className="scanner-hint">{note || 'Aponte para o QR que a outra pessoa está mostrando.'}</p>
        </>
      )}
      <button className="btn" onClick={onClose}>Fechar</button>
    </div>
  );
}
