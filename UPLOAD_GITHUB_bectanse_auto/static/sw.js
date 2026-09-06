// Bectanse Académie — Service Worker PWA + Web Push
const CACHE = 'bectanse-academie-v10';
const ASSETS = ['/', '/dashboard', '/static/manifest.json'];
const BADGE_DB = 'bectanse-notification-state';
const BADGE_STORE = 'state';

function openBadgeDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(BADGE_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(BADGE_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readBadgeCount() {
  const db = await openBadgeDb();
  return new Promise(resolve => {
    const request = db.transaction(BADGE_STORE).objectStore(BADGE_STORE).get('unread');
    request.onsuccess = () => resolve(Number(request.result || 0));
    request.onerror = () => resolve(0);
  });
}

async function writeBadgeCount(value) {
  const db = await openBadgeDb();
  await new Promise(resolve => {
    const transaction = db.transaction(BADGE_STORE, 'readwrite');
    transaction.objectStore(BADGE_STORE).put(Math.max(0, Number(value || 0)), 'unread');
    transaction.oncomplete = resolve;
    transaction.onerror = resolve;
  });
}

async function setSystemBadge(value) {
  try {
    if (value > 0 && self.navigator && 'setAppBadge' in self.navigator) {
      await self.navigator.setAppBadge(value);
    } else if (self.navigator && 'clearAppBadge' in self.navigator) {
      await self.navigator.clearAppBadge();
    }
  } catch (_) {}
}

async function incrementBadge() {
  const count = (await readBadgeCount()) + 1;
  await writeBadgeCount(count);
  await setSystemBadge(count);
  return count;
}

async function clearBadge() {
  await writeBadgeCount(0);
  await setSystemBadge(0);
}

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
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

// ── WEB PUSH NOTIFICATIONS ──────────────────────────────────────────────────
self.addEventListener('push', e => {
  let data = { title: 'Bectanse Académie', body: 'Nouveau message sur le Canal VIP', url: '/canal' };
  try {
    if (e.data) data = { ...data, ...JSON.parse(e.data.text()) };
  } catch {}

  e.waitUntil((async () => {
    const badgeCount = await incrementBadge();
    await self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      vibrate: [200, 100, 200],
      tag: data.tag || ('bectanse-' + (data.url || 'notification')),
      renotify: true,
      silent: false,
      timestamp: Date.now(),
      data: { url: data.url, badgeCount }
    });
  })());
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/canal';
  e.waitUntil((async () => {
    await clearBadge();
    return clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    });
  })());
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'CLEAR_BADGE') {
    event.waitUntil(clearBadge());
  }
});
