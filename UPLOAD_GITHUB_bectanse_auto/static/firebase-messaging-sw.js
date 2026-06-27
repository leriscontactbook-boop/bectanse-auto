importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey: "AIzaSyD46fBm-SMtHiQ94lMzEcrYT9r3eIBqVwo",
  projectId: "bectanse-auto",
  messagingSenderId: "228109399118",
  appId: "1:228109399118:web:303109201c76ee180abf1f"
});

const messaging = firebase.messaging();

// Notification en arrière-plan
messaging.onBackgroundMessage(function(payload) {
  const title = payload.notification?.title || 'Bectanse AUTO';
  const body  = payload.notification?.body  || '';
  const icon  = '/static/icons/icon-192.png';
  self.registration.showNotification(title, {
    body, icon,
    badge: '/static/icons/icon-192.png',
    vibrate: [200, 100, 200],
    data: { url: payload.data?.url || '/accueil' }
  });
});

// Clic sur notification → ouvrir l'app
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  const url = event.notification.data?.url || '/accueil';
  event.waitUntil(clients.openWindow(url));
});
