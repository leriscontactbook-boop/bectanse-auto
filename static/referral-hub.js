(function () {
  'use strict';

  const hub = document.getElementById('referral-hub');
  if (!hub) return;

  const referralUrl = hub.dataset.referralUrl || '';
  const totalReferrals = Number.parseInt(hub.dataset.total || '0', 10) || 0;
  const referralMessage = hub.dataset.referralMessage || referralUrl;
  const toast = document.getElementById('ref-toast');
  let toastTimer;

  function announce(message, isError) {
    if (!toast) return;
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', Boolean(isError));
    toast.classList.add('is-visible');
    toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 2400);
  }

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const field = document.createElement('textarea');
    field.value = value;
    field.setAttribute('readonly', '');
    field.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand('copy');
    field.remove();
    if (!copied) throw new Error('copy-failed');
  }

  function trackShare(channel) {
    if (typeof window.bectanseTrack === 'function') {
      window.bectanseTrack('cta_click', {
        label: 'parrainage_share',
        channel: channel,
        referral_level: hub.dataset.level || 'Starter'
      });
    }
  }

  async function shareReferral(channel) {
    trackShare(channel);
    if (channel === 'native' && navigator.share) {
      try {
        await navigator.share({
          title: 'Découvrir Bectanse Académie',
          text: referralMessage.replace(referralUrl, '').trim(),
          url: referralUrl
        });
        announce('Invitation prête à être envoyée');
        return;
      } catch (error) {
        if (error && error.name === 'AbortError') return;
      }
    }

    if (channel === 'whatsapp') {
      window.open('https://wa.me/?text=' + encodeURIComponent(referralMessage), '_blank', 'noopener,noreferrer');
      return;
    }
    if (channel === 'telegram') {
      window.open('https://t.me/share/url?url=' + encodeURIComponent(referralUrl) + '&text=' + encodeURIComponent(referralMessage.replace(referralUrl, '').trim()), '_blank', 'noopener,noreferrer');
      return;
    }

    try {
      await copyText(channel === 'message' ? referralMessage : referralUrl);
      announce(channel === 'message' ? 'Message complet copié' : 'Lien personnel copié');
    } catch (_) {
      announce('Impossible de copier automatiquement', true);
    }
  }

  document.querySelectorAll('[data-share]').forEach(button => {
    button.addEventListener('click', () => shareReferral(button.dataset.share || 'copy'));
  });

  const range = document.getElementById('ref-sim-range');
  const rangeValue = document.getElementById('ref-sim-count');
  const estimatedValue = document.getElementById('ref-sim-value');
  const estimatedLevel = document.getElementById('ref-sim-level');

  function estimatePotential(count) {
    if (count >= 20) return {amount: count * 100 + 2000, level: 'Projection Elite · 100 € par filleul + bonus 2 000 €'};
    if (count >= 10) return {amount: count * 75 + 1000, level: 'Projection Ambassador · 75 € par filleul + bonus 1 000 €'};
    if (count >= 5) return {amount: count * 50 + 250, level: 'Projection Bronze · 50 € par filleul + bonus 250 €'};
    return {amount: count * 50, level: count ? 'Commission de base · 50 € par filleul validé' : 'Déplace le curseur pour simuler ton potentiel'};
  }

  function renderPotential() {
    if (!range || !rangeValue || !estimatedValue || !estimatedLevel) return;
    const count = Number.parseInt(range.value, 10) || 0;
    const result = estimatePotential(count);
    rangeValue.textContent = String(count);
    estimatedValue.textContent = result.amount.toLocaleString('fr-FR') + ' €';
    estimatedLevel.textContent = result.level;
  }
  range && range.addEventListener('input', renderPotential);
  renderPotential();

  const progressFill = document.getElementById('ref-progress-fill');
  window.requestAnimationFrame(() => {
    if (progressFill) progressFill.style.width = Math.min(totalReferrals / 20 * 100, 100) + '%';
  });

  const tabs = document.querySelectorAll('[data-payment-tab]');
  function activatePaymentTab(type) {
    tabs.forEach(tab => {
      const active = tab.dataset.paymentTab === type;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
    });
    document.querySelectorAll('[data-payment-panel]').forEach(panel => {
      panel.hidden = panel.dataset.paymentPanel !== type;
    });
  }
  tabs.forEach(tab => tab.addEventListener('click', () => activatePaymentTab(tab.dataset.paymentTab)));

  async function savePayment(type, button) {
    const status = document.getElementById('ref-form-status');
    const state = document.getElementById('ref-payment-state');
    const original = button.textContent;
    const payload = {type: type};
    if (type === 'virement') {
      payload.titulaire = document.getElementById('pay-titulaire').value.trim();
      payload.iban = document.getElementById('pay-iban').value.trim();
      payload.bic = document.getElementById('pay-bic').value.trim();
    } else {
      payload.reseau = document.getElementById('pay-reseau').value;
      payload.adresse = document.getElementById('pay-adresse').value.trim();
    }

    status.textContent = '';
    status.classList.remove('is-success');
    button.disabled = true;
    button.textContent = 'Enregistrement…';
    try {
      const response = await fetch('/save-paiement', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || 'Enregistrement impossible.');
      status.textContent = 'Informations enregistrées et sécurisées.';
      status.classList.add('is-success');
      if (state) {
        state.classList.add('is-ready');
        state.innerHTML = '<i></i> Paiement configuré';
      }
      announce('Mode de paiement enregistré');
    } catch (error) {
      status.textContent = error.message || 'Enregistrement impossible.';
      announce(status.textContent, true);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  document.querySelectorAll('[data-save-payment]').forEach(button => {
    button.addEventListener('click', () => savePayment(button.dataset.savePayment, button));
  });

  const revealObserver = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        revealObserver.unobserve(entry.target);
      }
    });
  }, {threshold: .08}) : null;
  document.querySelectorAll('.ref-reveal').forEach(element => {
    if (revealObserver) revealObserver.observe(element);
    else element.classList.add('is-visible');
  });

  const mobileCta = document.getElementById('ref-mobile-cta');
  function syncMobileCta() {
    if (!mobileCta) return;
    const pushPrompt = document.getElementById('bectanse-push-required');
    const pushPromptVisible = Boolean(pushPrompt && window.getComputedStyle(pushPrompt).display !== 'none');
    document.body.classList.toggle('ref-has-push-prompt', pushPromptVisible);
    mobileCta.classList.toggle('is-visible', window.scrollY > 520 && !pushPromptVisible);
  }
  window.addEventListener('scroll', syncMobileCta, {passive: true});
  new MutationObserver(syncMobileCta).observe(document.body, {childList: true, subtree: true, attributes: true});
  syncMobileCta();
})();
