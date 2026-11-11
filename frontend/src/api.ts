export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, options: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Requested-With': 'album' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: options.method ?? (options.body !== undefined || options.form ? 'POST' : 'GET'),
      headers,
      credentials: 'same-origin',
      body: options.form ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    });
  } catch {
    throw new ApiError(0, 'Sem conexão. Confira sua internet e tente de novo.');
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    if (response.status >= 502 && typeof detail !== 'string') {
      throw new ApiError(response.status, 'O servidor está fora do ar. Tente de novo em instantes.');
    }
    throw new ApiError(response.status, typeof detail === 'string' ? detail : 'Confira os dados e tente de novo.');
  }
  return data as T;
}

export const post = <T = any>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body });

export interface Card {
  id: number;
  number: number;
  name: string;
  image_url: string | null;
  size: 'P' | 'M' | 'G';
  tag: string;
  rarity: { name: string; rank: number; color: string };
  org_id: number;
  org_name: string;
  org_color: string;
  page_no: number;
  is_mosaic_piece: boolean;
  mosaic_index: number | null;
}

export interface PageMeta {
  kind: 'stickers' | 'mosaic';
  org_id: number;
  page_no: number;
  org_name: string;
  color: string;
  total: number;
  filled: number;
}

export interface AlbumInfo {
  id: number;
  title: string;
  cover_url: string | null;
  starts_at: string;
  ends_at: string;
  status: 'draft' | 'scheduled' | 'active' | 'ended';
}

export interface AlbumStats {
  pasted: number;
  total_stickers: number;
  received_total: number;
  duplicates: number;
  trades_done: number;
  completed_at?: string;
}

export interface AlbumSummary extends AlbumInfo {
  joined_at: string | null;
  stats: AlbumStats;
}

export interface MyAlbums {
  active: AlbumSummary | null;
  ended: AlbumSummary[];
  upcoming: { title: string; starts_at: string } | null;
  server_now: string;
}

export interface AlbumIndex {
  album: AlbumInfo | null; // null = nenhum álbum em andamento
  read_only: boolean;
  pages: PageMeta[];
  orgs: { id: number; name: string; color: string; logo_url: string | null; total: number; filled: number }[];
  total: number;
  filled: number;
  tray: Card[];
}

export interface Slot {
  sticker_id: number;
  number: number;
  size: 'P' | 'M' | 'G';
  layout: { x: number; y: number; w: number; h: number };
  mosaic_index: number | null;
  hint: string;
  filled: boolean;
  card?: Card;
}

export interface PageDetail {
  kind: 'stickers' | 'mosaic';
  page_no: number;
  org: {
    id: number;
    name: string;
    description: string;
    logo_url: string | null;
    color: string;
    mosaic_image_url: string | null;
    mosaic_cols: number;
    mosaic_rows: number;
  };
  slots: Slot[];
}

export interface User {
  id: number;
  email: string;
  nickname: string;
  verified: boolean;
  is_admin: boolean;
}

export interface GrantView {
  id: number;
  source: 'daily' | 'code' | 'admin';
  name: string;
  slug: string;
  stickers_per_pack: number;
}

export interface TradeView {
  token: string;
  state: 'invite' | 'open' | 'active' | 'done' | 'cancelled' | 'expired';
  is_creator: boolean;
  other_nickname: string | null;
  offer_version: number;
  my_offer: (Card & { quantity: number })[];
  other_offer: (Card & { quantity: number })[];
  i_accepted: boolean;
  other_accepted: boolean;
  expires_in: number;
}
