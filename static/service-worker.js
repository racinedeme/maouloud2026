/*
 * IMPORTANT: this app requires a live connection to the server for its data
 * (cotisations, dépenses, bus...) and is redeployed frequently. Caching the
 * app shell (index.html + its inline JS) is high-risk and low-value here —
 * it's exactly what caused deployed updates to not show up. So the app
 * shell is NEVER cached and NEVER served from cache: every navigation goes
 * straight to the network. Only small static assets (icons, manifest) are
 * cached, purely so "Add to Home Screen" has an icon to show, and even
 * those are network-first with cache used only as a true offline fallback.
 */
const CACHE_NAME = 'maouloud2026-v3';
const APP_SHELL = ['/manifest.json', '/logo-barobe.png', '/icons/icon-192.png', '/icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never touch API calls — data must always come straight from the network.
  if (url.pathname.startsWith('/api/')) return;

  // Never cache or intercept the app shell — always go straight to the network,
  // with no cache fallback at all, so redeployed updates are always visible
  // immediately. If there's truly no network, let the browser's own offline
  // error show rather than silently serving a stale, possibly very outdated app.
  const isAppShell = event.request.mode === 'navigate' || url.pathname === '/' || url.pathname.endsWith('.html');
  if (isAppShell) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Network-first for the small set of static assets, cache used only as a
  // true offline fallback.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});
