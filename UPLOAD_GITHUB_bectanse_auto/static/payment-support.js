(() => {
  'use strict';
  const selector = 'a[href*="/checkout/"],a[href^="https://buy.stripe.com/"],[data-open-portal],[data-open-billing],button[data-pack],.autumn-modal > .autumn-cta,.autumn-hero-actions';
  function addSupport() {
    document.querySelectorAll(selector).forEach(control => {
      if (control.closest('[data-payment-help]')) return;
      let host = control.closest('article,.pack,.tj-paywall,.tj-locked,.actions,.autumn-modal,.autumn-hero-copy') || control.parentElement;
      // Whole plan cards are links: keep the contact link outside them.
      if (host.closest('a')) host = host.closest('a').parentElement;
      if (host.querySelector(':scope > [data-payment-help]')) return;
      const help = document.createElement('div'); help.className = 'payment-help'; help.dataset.paymentHelp = 'true';
      const text = document.createElement('span'); text.textContent = 'Une question avant de payer ?';
      const link = document.createElement('a'); link.href = 'https://t.me/lerisluketobot'; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Parler à un agent';
      const note = document.createElement('small'); note.textContent = 'Échange avec l’équipe ou demande un rendez-vous.';
      help.append(text,link,note); host.append(help);
    });
  }
  let queued = false;
  new MutationObserver(() => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; addSupport(); }); }).observe(document.body,{childList:true,subtree:true});
  addSupport();
})();
