// Bectanse AUTO — client Web Push unique (iPhone, Android et ordinateur)
(function () {
  'use strict';

  function base64ToBytes(value) {
    const padding = '='.repeat((4 - value.length % 4) % 4);
    const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, character => character.charCodeAt(0));
  }

  function sameApplicationServerKey(subscription, expectedBytes) {
    const current = subscription && subscription.options && subscription.options.applicationServerKey;
    if (!current) return false;
    const currentBytes = new Uint8Array(current);
    if (currentBytes.length !== expectedBytes.length) return false;
    return currentBytes.every((value, index) => value === expectedBytes[index]);
  }

  async function getCurrentApplicationServerKey() {
    const response = await fetch('/api/push/vapid-public', {
      credentials: 'same-origin', cache: 'no-store'
    });
    if (!response.ok) throw new Error('Clé de notification indisponible');
    const {key} = await response.json();
    return base64ToBytes(key);
  }

  function isIOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  }

  async function registerSubscription(subscription) {
    const response = await fetch('/api/push/subscribe', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(subscription.toJSON())
    });
    if (!response.ok) throw new Error('Enregistrement de l’appareil impossible');
  }

  async function initPushNotifications(options = {}) {
    const requestPermission = options.requestPermission === true;
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      return {ok: false, reason: 'unsupported'};
    }
    if (isIOS() && !isStandalone()) {
      return {ok: false, reason: 'install-required'};
    }

    const registration = await navigator.serviceWorker.register('/sw.js', {scope: '/'});
    await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();

    // Une rotation VAPID rend les anciens abonnements Apple inutilisables.
    // Compare la clé réellement liée à l'appareil et renouvelle silencieusement
    // l'abonnement si l'autorisation système est toujours accordée.
    if (subscription && Notification.permission === 'granted') {
      const currentKey = await getCurrentApplicationServerKey();
      if (!sameApplicationServerKey(subscription, currentKey)) {
        try {
          await fetch('/api/push/unsubscribe', {
            method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({endpoint: subscription.endpoint})
          });
        } catch (_) {}
        await subscription.unsubscribe();
        subscription = null;
      }
    }

    // Une visite ne déclenche jamais la fenêtre système. Seul un clic volontaire le fait.
    if (!subscription && Notification.permission === 'default' && !requestPermission) {
      return {ok: false, reason: 'permission-required'};
    }
    if (Notification.permission === 'denied') {
      return {ok: false, reason: 'denied'};
    }
    if (!subscription && requestPermission) {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') return {ok: false, reason: permission};
    }

    if (!subscription && Notification.permission === 'granted') {
      const applicationServerKey = await getCurrentApplicationServerKey();
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey
      });
    }
    if (subscription) {
      await registerSubscription(subscription);
      return {ok: true, reason: 'subscribed'};
    }
    return {ok: false, reason: 'permission-required'};
  }

  async function enablePushNotifications() {
    try {
      const result = await initPushNotifications({requestPermission: true});
      if (result.ok) {
        const registration = await navigator.serviceWorker.ready;
        const currentSubscription = await registration.pushManager.getSubscription();
        const test = await fetch('/api/push/test', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({endpoint: currentSubscription ? currentSubscription.endpoint : ''})
        });
        result.testSent = test.ok;
      }
      return result;
    } catch (error) {
      console.warn('Bectanse Web Push:', error);
      return {ok: false, reason: 'error', error: error.message};
    }
  }

  window.initPushNotifications = initPushNotifications;
  window.enablePushNotifications = enablePushNotifications;
  window.BectansePush = {init: initPushNotifications, enable: enablePushNotifications,
    isIOS, isStandalone};

  async function clearVisibleAppBadge() {
    if (!isStandalone() || document.visibilityState !== 'visible') return;
    try {
      if ('clearAppBadge' in navigator) await navigator.clearAppBadge();
      const registration = await navigator.serviceWorker.ready;
      if (registration.active) registration.active.postMessage({type: 'CLEAR_BADGE'});
    } catch (_) {}
  }

  // Réenregistre silencieusement un abonnement existant, sans demander d’autorisation.
  const start = () => {
    initPushNotifications().catch(error => console.warn('Bectanse Web Push:', error));
    clearVisibleAppBadge();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
  document.addEventListener('visibilitychange', clearVisibleAppBadge);
})();
