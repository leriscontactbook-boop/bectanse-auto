// Bectanse AUTO — client Web Push unique (iPhone, Android et ordinateur)
(function () {
  'use strict';

  function base64ToBytes(value) {
    const padding = '='.repeat((4 - value.length % 4) % 4);
    const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, character => character.charCodeAt(0));
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
      const keyResponse = await fetch('/api/push/vapid-public', {credentials: 'same-origin'});
      if (!keyResponse.ok) throw new Error('Clé de notification indisponible');
      const {key} = await keyResponse.json();
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64ToBytes(key)
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

  // Réenregistre silencieusement un abonnement existant, sans demander d’autorisation.
  const start = () => initPushNotifications().catch(error => console.warn('Bectanse Web Push:', error));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
