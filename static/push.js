// Bectanse AUTO — Web Push client
const VAPID_PUBLIC = "BI5TQpefuRvs_HIPgRzXnBQqcQ5V9puh2hteQmdRp8pQFMEh-XyvgPGpYrO5ioPak9Z7ml6laSl2WnNh96RFrv8";

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const arr = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) arr[i] = rawData.charCodeAt(i);
  return arr;
}

async function initPushNotifications() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
  try {
    const reg = await navigator.serviceWorker.register('/sw.js');
    await navigator.serviceWorker.ready;

    // Vérifier permission actuelle
    if (Notification.permission === 'denied') return;

    // Vérifier si déjà abonné
    const existing = await reg.pushManager.getSubscription();
    if (existing) {
      // Déjà abonné — s'assurer que c'est bien enregistré en base
      await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(existing.toJSON())
      });
      return;
    }

    // Demander permission si pas encore décidé
    if (Notification.permission === 'default') {
      // Attendre 3 secondes avant de demander (moins intrusif)
      await new Promise(r => setTimeout(r, 3000));
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') return;
    }

    // S'abonner
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC)
    });

    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(sub.toJSON())
    });
  } catch (e) {
    console.log('Push init:', e);
  }
}

// Lancer dès que le SW est prêt
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.ready.then(() => initPushNotifications()).catch(() => {});
}
