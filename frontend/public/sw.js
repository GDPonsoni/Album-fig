// Guarda só arquivos estáticos e imagens. A API nunca passa pelo cache:
// o servidor é a autoridade sobre pacotes, inventário e trocas.
const CACHE = 'album-v1';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function revalidate(request, cache) {
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  else await cache.delete(request); // figurinha removida a pedido sai do aparelho também
  return response;
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  const fresh = revalidate(request, cache);
  if (cached) {
    fresh.catch(() => {});
    return cached;
  }
  return fresh;
}

async function appShell(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put('/', response.clone());
    return response;
  } catch {
    return (await cache.match('/')) ?? Response.error();
  }
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  if (event.request.mode === 'navigate') event.respondWith(appShell(event.request));
  else if (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/media/') || url.pathname.startsWith('/icons/')) {
    event.respondWith(staleWhileRevalidate(event.request));
  }
});
