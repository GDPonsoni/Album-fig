// Converte o conteúdo do editor (rascunho ou publicado) no mesmo formato de página que o jogo usa,
// para a prévia passar pelo mesmo componente de renderização (components/AlbumPages.tsx).
import type { AssetView, Card, ChapterContent, OrgView, PageDetail, RarityView } from './api';

const FALLBACK_RARITY = { name: 'comum', rank: 1, color: '#6b7280' };

export function contentToPages(
  content: ChapterContent,
  org: OrgView,
  assets: AssetView[],
  rarities: RarityView[],
  numbers: Record<string, number> = {},
): PageDetail[] {
  const url = (id: number | null | undefined) => (id == null ? null : assets.find((asset) => asset.id === id)?.url ?? null);
  const orgView = {
    ...org,
    mosaic_image_url: url(content.mosaic?.asset_id),
    mosaic_cols: content.mosaic?.cols ?? 2,
    mosaic_rows: content.mosaic?.rows ?? 3,
    mosaic_scale: content.mosaic?.scale ?? 1,
    mosaic_rot: content.mosaic?.rot ?? 0,
  };
  const active = content.stickers.filter((sticker) => sticker.active);
  const pages: PageDetail[] = [...content.pages]
    .sort((a, b) => a.page_no - b.page_no)
    .map((page) => ({
      kind: 'stickers',
      page_no: page.page_no,
      orientation: content.layout.orientation,
      page: { title: page.title, text: page.text, background_url: url(page.background_asset_id), layout: page.layout },
      org: orgView,
      slots: active
        .filter((sticker) => sticker.page_no === page.page_no)
        .map((sticker) => {
          const index = content.stickers.indexOf(sticker);
          const rarity = rarities.find((r) => r.id === sticker.suggested_rarity_id) ?? rarities[0] ?? FALLBACK_RARITY;
          // Número definitivo só existe depois da publicação; antes, a prévia mostra a ordem.
          const number = numbers[sticker.key] ?? index + 1;
          const card: Card = {
            id: -(index + 1),
            number,
            name: sticker.name,
            image_url: url(sticker.asset_id),
            size: sticker.size,
            tag: sticker.tag,
            rarity: { name: rarity.name, rank: rarity.rank, color: rarity.color },
            org_id: org.id,
            org_name: org.name,
            org_color: org.color,
            page_no: sticker.page_no,
            is_mosaic_piece: false,
            mosaic_index: null,
          };
          // Sem imagem, a prévia mostra o espaço vazio com a dica, como o jogador veria antes de colar.
          return { sticker_id: card.id, number, size: sticker.size, layout: sticker.slot, mosaic_index: null, hint: sticker.hint, filled: !!card.image_url, card };
        }),
    }));
  if (content.mosaic?.asset_id != null) {
    const total = content.mosaic.cols * content.mosaic.rows;
    pages.push({
      kind: 'mosaic',
      page_no: 0,
      orientation: content.layout.orientation,
      org: orgView,
      slots: Array.from({ length: total }, (_, index) => ({
        sticker_id: -(1000 + index), number: index + 1, size: 'P', layout: { x: 0, y: 0, w: 0, h: 0 }, mosaic_index: index, hint: '', filled: true,
      })),
    });
  }
  return pages;
}

/** Espaços de uma grade predefinida, em % da página (abaixo do cabeçalho). */
export function gridSlots(layout: string, orientation: 'portrait' | 'landscape'): { x: number; y: number; w: number; h: number }[] {
  const match = /^grid-(\d)x(\d)$/.exec(layout);
  if (!match) return [];
  const cols = Number(match[1]);
  const rows = Number(match[2]);
  const top = orientation === 'landscape' ? 22 : 16;
  const left = 5;
  const gap = 2.5;
  const cellW = (100 - 2 * left - gap * (cols - 1)) / cols;
  const cellH = (100 - top - 4 - gap * (rows - 1)) / rows;
  // Figurinha tem proporção 3:4 (largura:altura) em pixels; a página não é quadrada, então corrige pela proporção dela.
  const pageRatio = orientation === 'landscape' ? 1.38 : 1 / 1.38; // largura / altura da página
  let w = cellW;
  let h = (w * pageRatio * 4) / 3;
  if (h > cellH) {
    h = cellH;
    w = (h * 3) / 4 / pageRatio;
  }
  const slots = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const x = left + col * (cellW + gap) + (cellW - w) / 2;
      const y = top + row * (cellH + gap) + (cellH - h) / 2;
      slots.push({ x: round(x), y: round(y), w: round(w), h: round(h) });
    }
  }
  return slots;
}

const round = (value: number) => Math.round(value * 10) / 10;
