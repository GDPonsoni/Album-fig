// Fica num módulo à parte para o jsqr só ser baixado por quem não tem BarcodeDetector.
import jsQR from 'jsqr';

/** Decodifica um quadro RGBA (o mesmo formato de `ImageData`). Fallback de quem não tem BarcodeDetector. */
export function decodeFrame(data: Uint8ClampedArray, width: number, height: number): string | null {
  return jsQR(data, width, height, { inversionAttempts: 'dontInvert' })?.data || null;
}
