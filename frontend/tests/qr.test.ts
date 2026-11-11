// Uso: npm test (Node 22 roda TypeScript direto). Não precisa de câmera nem de navegador.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import QRCode from 'qrcode';
import { parseTradeLink } from '../src/qr.ts';
import { decodeFrame } from '../src/qrDecode.ts';

const ORIGIN = 'https://album.example';
const TOKEN = 'q5Zr0n1Xb7_LkP-9sT2uVw4yAbCdEfGh'; // mesmo formato de secrets.token_urlsafe(24)

/** Desenha o QR como a câmera o veria: pixels RGBA, com margem branca e módulos ampliados. */
function render(text: string, scale = 6, margin = 4) {
  const { modules } = QRCode.create(text, { errorCorrectionLevel: 'M' });
  const size = (modules.size + margin * 2) * scale;
  const data = new Uint8ClampedArray(size * size * 4).fill(255);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const col = Math.floor(x / scale) - margin;
      const row = Math.floor(y / scale) - margin;
      const inside = col >= 0 && row >= 0 && col < modules.size && row < modules.size;
      if (inside && modules.get(row, col)) data.fill(0, (y * size + x) * 4, (y * size + x) * 4 + 3);
    }
  }
  return { data, size };
}

test('decodifica um QR gerado pela lib qrcode', () => {
  const link = `${ORIGIN}/trocar/${TOKEN}`;
  const { data, size } = render(link);
  assert.equal(decodeFrame(data, size, size), link);
});

test('quadro sem QR devolve null', () => {
  const size = 120;
  assert.equal(decodeFrame(new Uint8ClampedArray(size * size * 4).fill(200), size, size), null);
});

test('do QR lido até o caminho de troca', () => {
  const { data, size } = render(`${ORIGIN}/trocar/${TOKEN}`);
  assert.equal(parseTradeLink(decodeFrame(data, size, size)!, ORIGIN), `/trocar/${TOKEN}`);
});

test('só aceita link de troca da própria origem', () => {
  assert.equal(parseTradeLink(`  ${ORIGIN}/trocar/${TOKEN}\n`, ORIGIN), `/trocar/${TOKEN}`);
  for (const bad of [
    `https://outro.example/trocar/${TOKEN}`,
    `http://album.example/trocar/${TOKEN}`,
    `https://album.example.evil.test/trocar/${TOKEN}`,
    `https://user:pw@album.example/trocar/${TOKEN}`,
    `${ORIGIN}/trocar/${TOKEN}?next=//evil.test`,
    `${ORIGIN}/trocar/${TOKEN}#x`,
    `${ORIGIN}/trocar/${TOKEN}/extra`,
    `${ORIGIN}/trocar/curto`,
    `${ORIGIN}/r/ABCD-EFGH-2345-6789`,
    `${ORIGIN}/admin`,
    `javascript:alert(1)`,
    `/trocar/${TOKEN}`,
    'texto qualquer',
    '',
  ]) {
    assert.equal(parseTradeLink(bad, ORIGIN), null, bad);
  }
});
