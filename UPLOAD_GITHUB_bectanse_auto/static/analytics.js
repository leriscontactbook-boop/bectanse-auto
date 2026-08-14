(function () {
  'use strict';
  if (navigator.doNotTrack === '1' || localStorage.getItem('bectanse_analytics_optout') === '1') return;
  if (/^\/(admin|analyse-ia|api|static)(\/|$)/.test(location.pathname)) return;
  const makeId = () => (crypto && crypto.randomUUID)
    ? crypto.randomUUID().replace(/-/g, '')
    : Date.now().toString(36) + Math.random().toString(36).slice(2, 14);
  const visitorId = localStorage.getItem('bectanse_visitor_id') || makeId();
  localStorage.setItem('bectanse_visitor_id', visitorId);
  const sessionId = sessionStorage.getItem('bectanse_session_id') || makeId();
  sessionStorage.setItem('bectanse_session_id', sessionId);
  const query = new URLSearchParams(location.search);
  let attribution = {};
  try { attribution = JSON.parse(sessionStorage.getItem('bectanse_attribution') || '{}'); } catch (_) {}
  if (!attribution.source) {
    let source = query.get('utm_source') || '';
    if (!source && document.referrer) {
      try { source = new URL(document.referrer).hostname.replace(/^www\./, ''); } catch (_) {}
    }
    attribution = {source: source || 'direct', medium: query.get('utm_medium') || '', campaign: query.get('utm_campaign') || ''};
    sessionStorage.setItem('bectanse_attribution', JSON.stringify(attribution));
  }
  function track(eventName, properties) {
    fetch('/api/analytics/event', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({visitor_id: visitorId, session_id: sessionId, event_name: eventName,
        page_path: location.pathname, referrer: document.referrer || '', source: attribution.source || 'direct',
        medium: attribution.medium || '', campaign: attribution.campaign || '', properties: properties || {}})
    }).catch(function () {});
  }
  window.bectanseTrack = track;
  const labelFor = el => (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100);
  document.addEventListener('click', function (event) {
    const el = event.target.closest('a,button');
    if (!el) return;
    const href = (el.getAttribute('href') || '').slice(0, 250);
    const label = labelFor(el);
    const combined = (label + ' ' + href).toLowerCase();
    let eventName = 'cta_click';
    if (combined.includes('t.me/') || combined.includes('telegram')) eventName = 'telegram_click';
    else if (href.includes('/inscription')) eventName = 'registration_start';
    else if (combined.includes('stripe') || combined.includes('paiement') || combined.includes('payer')) eventName = 'checkout_start';
    else if (combined.includes('explor')) eventName = 'app_explore';
    else if (combined.includes('notification')) eventName = 'notification_interest';
    track(eventName, {label: label, destination: href.split('?')[0]});
  }, true);
  document.addEventListener('submit', function () {
    track(location.pathname === '/inscription' ? 'registration_submit' : 'form_submit');
  }, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => track('page_view'), {once: true});
  else track('page_view');
})();
