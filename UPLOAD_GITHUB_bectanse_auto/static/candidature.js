(() => {
  'use strict';
  const q = s => document.querySelector(s), form = q('#application');
  const csrf = q('meta[name="csrf-token"]').content;
  let step = 0, busy = false, polling = false, active = false, cooldown = 0;
  let verified = {email: false, phone: false};
  const err = text => { q('#form-error').textContent = text; };
  async function api(path, data) {
    const response = await fetch('/api/candidature/' + path, {
      method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
      ...(data === undefined ? {} : {body: JSON.stringify(data)})
    });
    let result;
    try { result = await response.json(); }
    catch { throw Error('Service temporairement indisponible. Réessaie plus tard.'); }
    if (!response.ok || !result.ok) throw Object.assign(Error(result.error || 'Impossible de terminer cette étape.'), {status: response.status});
    return result;
  }
  function controls() {
    q('#next0').disabled = q('#next1').disabled = busy;
    document.querySelectorAll('[data-back]').forEach(b => { b.disabled = busy; });
    const remaining = Math.max(0, Math.ceil((cooldown - Date.now()) / 1000));
    q('#verify-email').disabled = busy || verified.email;
    q('#email-code').disabled = verified.email;
    q('#resend-email').disabled = busy || verified.email || remaining > 0;
    q('#resend-email').textContent = verified.email ? 'Adresse confirmée' : remaining > 0 ? 'Nouvel envoi dans ' + remaining + ' s' : 'Renvoyer le code e-mail';
    q('#prepare-telegram').disabled = busy || !verified.email || verified.phone;
    q('#prepare-telegram').hidden = verified.phone || q('#open-telegram').hasAttribute('href');
    q('#open-telegram').hidden = verified.phone || !q('#open-telegram').hasAttribute('href');
    q('#check-telegram').disabled = busy || !verified.email || verified.phone;
    q('#check-telegram').hidden = verified.phone;
    q('#send').disabled = busy || !verified.email || !verified.phone;
    for (const channel of ['email', 'phone']) {
      q('#' + channel + '-state').textContent = verified[channel] ? '✓ Vérifié' : 'À vérifier';
      q('#' + channel + '-state').closest('.verify').classList.toggle('done', verified[channel]);
    }
  }
  function show(n, scroll = true) {
    step = n;
    document.querySelectorAll('[data-step]').forEach(e => { e.hidden = Number(e.dataset.step) !== n; });
    for (let i = 0; i < 3; i++) q('#nav' + i).classList.toggle('active', i === n);
    q('#bar').style.width = ((n + 1) / 3 * 100) + '%';
    if (scroll) q('article').scrollIntoView({behavior: 'smooth', block: 'start'});
    controls();
  }
  function valid(n) {
    for (const input of q('[data-step="' + n + '"]').querySelectorAll('input,textarea')) {
      if (!input.checkValidity()) { input.reportValidity(); return false; }
    }
    return true;
  }
  async function action(fn) {
    if (busy) return;
    busy = true; err(''); controls();
    try { await fn(); } catch (error) { err(error.message); }
    finally { busy = false; controls(); }
  }
  function reset() {
    active = false; verified = {email: false, phone: false}; cooldown = 0;
    q('#feedback').textContent = ''; q('#email-code').value = '';
    q('#open-telegram').removeAttribute('href'); controls();
  }
  function success() {
    active = false; form.hidden = true; q('#success').hidden = false;
    clearInterval(timer); clearInterval(poller);
  }
  function applyStatus(result) {
    verified.email = result.email_verified; verified.phone = result.phone_verified;
    if (result.submitted) { success(); return; }
    if (verified.phone) q('#feedback').textContent = 'Ton numéro est confirmé. Tu peux envoyer ta candidature.';
    controls();
  }
  async function prepareTelegram() {
    const result = await api('telegram/start', {});
    if (result.verified) { verified.phone = true; controls(); return; }
    // The server creates an opaque one-use link, never a browser-side proof.
    const url = new URL(result.url);
    if (url.origin !== 'https://t.me') throw Error('Lien Telegram indisponible.');
    q('#open-telegram').href = url.href;
    q('#feedback').textContent = 'Ouvre Telegram avec le bouton ci-dessus, partage ton numéro, puis reviens ici.';
  }
  q('#next0').onclick = () => { if (valid(0)) { err(''); show(1); } };
  q('#next1').onclick = () => {
    if (!valid(1) || !valid(0)) return;
    action(async () => {
      reset(); const data = Object.fromEntries(new FormData(form));
      const result = await api('start', data); active = true;
      q('#email-target').textContent = data.email; q('#phone-target').textContent = data.phone;
      cooldown = Date.now() + 60000; show(2);
      if (result.error) err(result.error);
    });
  };
  q('#verify-email').onclick = () => action(async () => {
    await api('verify', {channel: 'email', code: q('#email-code').value});
    verified.email = true; await prepareTelegram();
  });
  q('#resend-email').onclick = () => action(async () => {
    await api('send', {channel: 'email'}); cooldown = Date.now() + 60000;
    q('#feedback').textContent = 'Code e-mail envoyé.';
  });
  q('#prepare-telegram').onclick = () => action(prepareTelegram);
  q('#check-telegram').onclick = () => action(async () => {
    applyStatus(await api('status'));
    if (!verified.phone) {
      q('#feedback').textContent = 'Confirmation encore attendue. Partage ton numéro dans le bot, ou prépare un nouveau lien si le précédent a expiré.';
      q('#open-telegram').removeAttribute('href');
    }
  });
  document.querySelectorAll('[data-back]').forEach(button => {
    button.onclick = () => { if (!busy) { reset(); err(''); show(Number(button.dataset.back)); } };
  });
  form.onsubmit = event => {
    event.preventDefault();
    if (!verified.email || !verified.phone || step !== 2) return;
    action(async () => { await api('submit', {}); success(); q('article').scrollIntoView({behavior: 'smooth', block: 'start'}); });
  };
  async function poll() {
    if (!active || step !== 2 || !verified.email || verified.phone || polling || busy || document.hidden) return;
    polling = true;
    try { applyStatus(await api('status')); }
    catch (error) { if (error.status === 410) { active = false; err(error.message); } }
    finally { polling = false; }
  }
  const timer = setInterval(controls, 1000), poller = setInterval(poll, 5000);
  document.addEventListener('visibilitychange', poll);
  controls();
  // Resume only the same signed browser session after returning from Telegram.
  action(async () => {
    let result;
    try { result = await api('status'); } catch (error) { if (error.status === 410) return; throw error; }
    if (result.data) {
      for (const [key, value] of Object.entries(result.data)) {
        const field = form.elements.namedItem(key); if (field) field.value = value;
      }
      q('#email-target').textContent = result.data.email; q('#phone-target').textContent = result.data.phone;
      active = true; show(2, false);
    }
    applyStatus(result);
  });
})();
