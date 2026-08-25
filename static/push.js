// Bectanse AUTO — client Web Push unique (iPhone, Android et ordinateur)
(function () {
  'use strict';
  if (window.__bectansePushLoaded) return;
  window.__bectansePushLoaded = true;
  if (window.BECTANSE_PUSH_ELIGIBLE === false) return;

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

  function removeActivationPrompt() {
    const prompt = document.getElementById('bectanse-push-required');
    if (prompt) prompt.remove();
  }

  function showActivationPrompt(reason) {
    if (document.getElementById('pwa-popup') || document.getElementById('bectanse-push-required')) return;
    if (reason !== 'permission-required' && reason !== 'denied') return;
    const prompt = document.createElement('aside');
    prompt.id = 'bectanse-push-required';
    prompt.setAttribute('role', 'status');
    prompt.style.cssText = 'position:fixed;left:14px;right:14px;bottom:82px;z-index:9900;display:flex;align-items:center;justify-content:space-between;gap:12px;max-width:520px;margin:auto;padding:13px 14px;border:1px solid rgba(255,100,30,.46);border-radius:16px;background:rgba(12,13,12,.97);box-shadow:0 18px 55px rgba(0,0,0,.55);color:#fff;font-family:Inter,Arial,sans-serif;backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)';
    const message = document.createElement('div');
    message.innerHTML = reason === 'denied'
      ? '<b style="font-size:12px">🔕 Alertes VIP bloquées</b><small style="display:block;margin-top:4px;color:#a7aaa5;font-size:10px;line-height:1.35">Réactive Bectanse dans Réglages › Notifications.</small>'
      : '<b style="font-size:12px">🔔 Ne manque aucun signal VIP</b><small style="display:block;margin-top:4px;color:#a7aaa5;font-size:10px;line-height:1.35">Active les alertes sur cet appareil.</small>';
    prompt.appendChild(message);
    if (reason !== 'denied') {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'ACTIVER';
      button.style.cssText = 'min-height:38px;padding:0 14px;border:1px solid #ff6a22;border-radius:10px;background:#ff5a16;color:#fff;font:800 11px Inter,Arial,sans-serif;letter-spacing:.06em;cursor:pointer';
      button.addEventListener('click', async () => {
        button.disabled = true;
        button.textContent = 'ACTIVATION…';
        const result = await enablePushNotifications();
        if (result.ok) removeActivationPrompt();
        else {
          button.disabled = false;
          button.textContent = result.reason === 'denied' ? 'BLOQUÉ' : 'RÉESSAYER';
          if (result.reason === 'denied') {
            removeActivationPrompt();
            showActivationPrompt('denied');
          }
        }
      });
      prompt.appendChild(button);
    }
    document.body.appendChild(prompt);
  }

  // Réenregistre silencieusement un abonnement existant, sans demander d’autorisation.
  const start = async () => {
    try {
      const result = await initPushNotifications();
      if (result.ok) removeActivationPrompt();
      else if ((isStandalone() || !isIOS()) && result.reason !== 'install-required') {
        showActivationPrompt(result.reason);
      }
    } catch (error) {
      console.warn('Bectanse Web Push:', error);
    }
    clearVisibleAppBadge();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
  document.addEventListener('visibilitychange', clearVisibleAppBadge);
})();
