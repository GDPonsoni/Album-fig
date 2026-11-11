import { useMemo, useState } from 'react';
import type { AssetView, ChapterContent, OrgView, RarityView } from '../api';
import { contentToPages } from '../preview';
import { MosaicPage, StickerPage } from './AlbumPages';

const noop = () => {};

/** Prévia fiel de um conteúdo (rascunho ou publicado): as mesmas páginas que o jogador vê. */
export function ContentPreview({ content, org, assets, rarities, numbers, page, onPage, label = 'Prévia' }: {
  content: ChapterContent;
  org: OrgView;
  assets: AssetView[];
  rarities: RarityView[];
  numbers?: Record<string, number>;
  page?: number; // índice controlado por quem usa (o editor acompanha a página em edição)
  onPage?: (index: number) => void;
  label?: string;
}) {
  const pages = useMemo(() => contentToPages(content, org, assets, rarities, numbers), [content, org, assets, rarities, numbers]);
  const [own, setOwn] = useState(0);
  const index = Math.min(page ?? own, Math.max(0, pages.length - 1));
  const go = (next: number) => (onPage ?? setOwn)(next);
  const detail = pages[index];
  const landscape = content.layout.orientation === 'landscape';
  return (
    <figure className="preview" aria-label={label}>
      <figcaption>
        <strong>{label}</strong>
        <span className="preview-nav">
          <button type="button" className="round" aria-label="Página anterior da prévia" disabled={index === 0} onClick={() => go(index - 1)}>‹</button>
          <small>{detail ? (detail.kind === 'mosaic' ? 'Mosaico' : `Página ${detail.page_no}`) : 'Sem páginas'} · {pages.length ? index + 1 : 0}/{pages.length}</small>
          <button type="button" className="round" aria-label="Próxima página da prévia" disabled={index >= pages.length - 1} onClick={() => go(index + 1)}>›</button>
        </span>
      </figcaption>
      <div className={`preview-stage${landscape ? ' landscape' : ''}`}>
        {detail ? (
          detail.kind === 'mosaic' ? <MosaicPage detail={detail} onOpen={noop} lastPasted={null} /> : <StickerPage detail={detail} onOpen={noop} lastPasted={null} />
        ) : null}
      </div>
    </figure>
  );
}
