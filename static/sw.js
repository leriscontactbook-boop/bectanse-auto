// Bectanse AUTO — Service Worker PWA
const CACHE = 'bectanse-auto-v1';
const ASSETS = ['/', '/dashboard', '/static/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Toujours aller chercher en réseau — fallback cache si offline
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
