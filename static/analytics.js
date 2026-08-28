(function () {
  'use strict';
  if (navigator.doNotTrack === '1' || localStorage.getItem('bectanse_analytics_optout') === '1') return;
  if (/^\/(admin|analyse-ia|api|static)(\/|$)/.test(location.pathname)) return;
  const makeId = () => (crypto && crypto.randomUUID)
    ? crypto.randomUUID().replace(/-/g, '')
    : Date.now().toString(36) + Math.random().toString(36).slice(2, 14);
  const visitorId = localStorage.getItem('bectanse_visitor_id') || makeId();
  localStorage.setItem('bectanse_visitor_id', visitorId);
  let sessionId = sessionStorage.getItem('bectanse_session_id') || makeId();
  function currentSessionId(allowRotation) {
    const now = Date.now();
    const lastActivity = Number(sessionStorage.getItem('bectanse_session_last_activity') || 0);
    if (allowRotation === false) return sessionId;
    if (allowRotation !== false && lastActivity && now - lastActivity > 30 * 60 * 1000) sessionId = makeId();
    sessionStorage.setItem('bectanse_session_id', sessionId);
    sessionStorage.setItem('bectanse_session_last_activity', String(now));
    return sessionId;
  }
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
  function track(eventName, properties, preserveSession) {
    fetch('/api/analytics/event', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({visitor_id: visitorId, session_id: currentSessionId(!preserveSession), event_name: eventName,
        page_path: location.pathname, referrer: document.referrer || '', source: attribution.source || 'direct',
        medium: attribution.medium || '', campaign: attribution.campaign || '', properties: properties || {}})
    }).catch(function () {});
  }
  window.bectanseTrack = track;
  document.querySelectorAll('a[href^="/abonnement/checkout/"]').forEach(function (link) {
    try {
      const destination = new URL(link.getAttribute('href'), location.origin);
      if (attribution.source) destination.searchParams.set('utm_source', attribution.source);
      if (attribution.medium) destination.searchParams.set('utm_medium', attribution.medium);
      if (attribution.campaign) destination.searchParams.set('utm_campaign', attribution.campaign);
      link.setAttribute('href', destination.pathname + destination.search);
    } catch (_) {}
  });
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
    else if (href.includes('/abonnement/checkout/') || combined.includes('stripe') || combined.includes('paiement') || combined.includes('payer')) eventName = 'checkout_start';
    else if (combined.includes('explor')) eventName = 'app_explore';
    else if (combined.includes('notification')) eventName = 'notification_interest';
    track(eventName, {label: label, destination: href.split('?')[0]});
  }, true);
  document.addEventListener('submit', function () {
    track(location.pathname === '/inscription' ? 'registration_submit' : 'form_submit');
  }, true);
  document.addEventListener('play', function (event) {
    const media = event.target;
    if (!media || !/^(VIDEO|AUDIO)$/.test(media.tagName)) return;
    track('media_play', {type: media.tagName.toLowerCase(), source: (media.currentSrc || '').split('?')[0].slice(0, 250)});
  }, true);
  document.addEventListener('toggle', function (event) {
    const details = event.target;
    if (details && details.tagName === 'DETAILS' && details.open) {
      track('faq_open', {label: labelFor(details.querySelector('summary') || details)});
    }
  }, true);

  let activeSeconds = 0;
  let engagedSent = false;
  let maxScroll = 0;
  const scrollMilestones = new Set();
  setInterval(function () {
    if (!document.hidden) activeSeconds += 5;
    if (!engagedSent && activeSeconds >= 15) {
      engagedSent = true;
      track('page_engaged', {active_seconds: activeSeconds});
    }
  }, 5000);
  function measureScroll() {
    const doc = document.documentElement;
    const available = Math.max(1, doc.scrollHeight - window.innerHeight);
    const depth = doc.scrollHeight <= window.innerHeight + 1 ? 100 : Math.round(window.scrollY / available * 100);
    maxScroll = Math.max(maxScroll, Math.min(100, depth));
    [25, 50, 75, 100].forEach(function (milestone) {
      if (maxScroll >= milestone && !scrollMilestones.has(milestone)) {
        scrollMilestones.add(milestone);
        track('scroll_depth', {depth: milestone});
      }
    });
  }
  window.addEventListener('scroll', measureScroll, {passive: true});
  window.addEventListener('pagehide', function () {
    measureScroll();
    track('page_exit', {active_seconds: activeSeconds, max_scroll: maxScroll}, true);
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => track('page_view'), {once: true});
  else track('page_view');
})();
