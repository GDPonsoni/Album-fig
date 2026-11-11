// Validação do conteúdo de um QR de troca. Pura; testada em tests/qr.test.ts.
const TRADE_PATH = /^\/trocar\/([A-Za-z0-9_-]{16,64})$/;

/** Aceita só link de troca desta mesma origem. Devolve o caminho interno ou null. */
export function parseTradeLink(text: string, origin: string): string | null {
  let url: URL;
  try {
    url = new URL(text.trim());
  } catch {
    return null;
  }
  if (url.origin !== origin || url.search || url.hash || url.username || url.password) return null;
  const match = TRADE_PATH.exec(url.pathname);
  return match ? `/trocar/${match[1]}` : null;
}
