// Validação do conteúdo de um QR lido pela câmera (troca ou código de pacote). Pura; testada em tests/qr.test.ts.
const TRADE_PATH = /^\/trocar\/([A-Za-z0-9_-]{16,64})$/;

const CODE = '[2-9A-HJKMNP-Z]{4}(?:-[2-9A-HJKMNP-Z]{4}){3}'; // mesmo alfabeto de app/services/codes.py
const REDEEM_PATH = new RegExp(`^/r/(${CODE})$`);
const BARE_CODE = new RegExp(`^${CODE}$`);

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

/** Link de código de pacote desta origem, ou o código puro. Devolve `/r/CÓDIGO` ou null. */
export function parseRedeemLink(text: string, origin: string): string | null {
  const value = text.trim();
  if (BARE_CODE.test(value)) return `/r/${value}`;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.origin !== origin || url.search || url.hash || url.username || url.password) return null;
  const match = REDEEM_PATH.exec(url.pathname);
  return match ? `/r/${match[1]}` : null;
}

/** Qualquer câmera do app entende os dois tipos de QR. `redeem` marca o que se resgata ao ler. */
export function parseQr(text: string, origin: string): { path: string; redeem: boolean } | null {
  const trade = parseTradeLink(text, origin);
  if (trade) return { path: trade, redeem: false };
  const code = parseRedeemLink(text, origin);
  return code ? { path: code, redeem: true } : null;
}
