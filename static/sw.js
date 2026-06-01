/* Revival Fire Ministries Portal — Service Worker */
// Bump the VERSION any time HTML / cached assets need to be force-refreshed
// on every client. The activate handler below wipes any cache that doesn't
// match the current VERSION, which is the only reliable way to evict stale
// templates from existing installs (display_submit.html in particular).
const VERSION = 'rfm-portal-v3';
const STATIC_CACHE = `${VERSION}-static`;
const RUNTIME_CACHE = `${VERSION}-runtime`;

// App shell — these are pre-cached so the app can boot offline.
const PRECACHE_URLS = [
  '/offline',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE && key !== RUNTIME_CACHE)
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

// Strategy:
//   - Navigations (HTML): network-first, fall back to cached version, then /offline.
//   - Same-origin static assets (/static/...): cache-first with background revalidation.
//   - API GETs: network-first, fall back to cache for read-only endpoints; never cache mutations.
//   - Everything else: passthrough.
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  // Page navigation
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((cache) => cache.put(req, copy));
          return res;
        })
        .catch(() =>
          caches.match(req).then((cached) => cached || caches.match('/offline'))
        )
    );
    return;
  }

  // Static assets
  if (sameOrigin && url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const fetchPromise = fetch(req)
          .then((res) => {
            if (res && res.status === 200) {
              const copy = res.clone();
              caches.open(STATIC_CACHE).then((cache) => cache.put(req, copy));
            }
            return res;
          })
          .catch(() => cached);
        return cached || fetchPromise;
      })
    );
    return;
  }

  // API reads — network first, cache fallback
  if (sameOrigin && url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(RUNTIME_CACHE).then((cache) => cache.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
  }
});

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

// ============ PUSH NOTIFICATIONS ============

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch (e) { try { data = { title: 'RFM Portal', body: event.data ? event.data.text() : '' }; } catch (_) {} }
  const title = data.title || 'RFM Portal';
  const options = {
    body: data.body || '',
    icon: data.icon || '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: data.tag || undefined,
    renotify: !!data.tag,
    data: { url: data.url || '/portal' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/portal';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      // Focus an existing tab if one is open at the target URL (or any portal page)
      for (const w of wins) {
        try {
          const u = new URL(w.url);
          if (u.pathname === targetUrl || u.pathname.startsWith('/portal')) {
            return w.focus().then(() => w.navigate ? w.navigate(targetUrl) : null);
          }
        } catch (e) { /* ignore */ }
      }
      return clients.openWindow(targetUrl);
    })
  );
});
