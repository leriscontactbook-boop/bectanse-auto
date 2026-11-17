/* Shared navigation; existing module actions and server authorization remain authoritative. */
(() => {
  const sidebar = document.querySelector('.aw-sidebar');
  if (!sidebar) return;
  const menu = document.querySelector('.aw-menu');
  const backdrop = document.querySelector('.aw-backdrop');
  const setOpen = open => {
    document.body.classList.toggle('aw-nav-open', open);
    menu.setAttribute('aria-expanded', String(open));
    sidebar.inert = !open && matchMedia('(max-width: 1100px)').matches;
    backdrop.hidden = !open;
    if (open) sidebar.querySelector('input').focus();
  };
  menu.addEventListener('click', () => setOpen(menu.getAttribute('aria-expanded') !== 'true'));
  backdrop.addEventListener('click', () => { setOpen(false); menu.focus(); });
  const search = document.getElementById('workspace-search');
  let searchDialog;
  const findSpace = () => {
    if (matchMedia('(max-width: 1100px)').matches) { setOpen(true); return; }
    if (!searchDialog) {
      searchDialog = document.createElement('dialog'); searchDialog.className = 'aw-search-dialog';
      searchDialog.setAttribute('aria-labelledby','aw-search-title');
      searchDialog.innerHTML = '<h2 id="aw-search-title">Trouver un espace</h2><input type="search" aria-label="Nom de l’espace" placeholder="Membres, facturation, Telegram…"><div class="aw-search-results"></div><button type="button">Fermer</button>';
      sidebar.querySelectorAll('.aw-link').forEach(link => {
        const clone = link.cloneNode(true); clone.hidden = false; clone.classList.remove('is-active'); clone.removeAttribute('aria-current');
        clone.addEventListener('click', () => searchDialog.close()); searchDialog.querySelector('.aw-search-results').append(clone);
      });
      const empty = document.createElement('p'); empty.className = 'aw-nav-empty'; empty.textContent = 'Aucun espace trouvé.'; empty.hidden = true; searchDialog.querySelector('.aw-search-results').append(empty);
      searchDialog.querySelector('input').addEventListener('input', e => {
        let count = 0; searchDialog.querySelectorAll('a').forEach(link => { link.hidden = !normalize(link.textContent).includes(normalize(e.target.value)); if (!link.hidden) count++; }); empty.hidden = count > 0;
      });
      searchDialog.querySelector('input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); searchDialog.querySelector('a:not([hidden])')?.click(); } });
      searchDialog.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); searchDialog.close(); } });
      searchDialog.querySelector('button').addEventListener('click', () => searchDialog.close()); document.body.append(searchDialog);
    }
    searchDialog.showModal(); searchDialog.querySelector('input').focus();
  };
  document.querySelector('.aw-find-space').addEventListener('click',findSpace);
  const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  search.addEventListener('input', () => {
    let count = 0;
    sidebar.querySelectorAll('.aw-nav-group').forEach(group => {
      let visible = 0;
      group.querySelectorAll('.aw-link').forEach(link => {
        link.hidden = !normalize(link.textContent).includes(normalize(search.value.trim()));
        if (!link.hidden) visible++;
      });
      group.hidden = visible === 0;
      count += visible;
    });
    sidebar.querySelector('.aw-nav-empty').hidden = count > 0;
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.body.classList.contains('aw-nav-open')) { setOpen(false); menu.focus(); }
    if (e.key === '/' && !e.ctrlKey && !e.metaKey && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName) && !e.target.isContentEditable) {
      e.preventDefault(); findSpace();
    }
    if (e.key === 'Tab' && document.body.classList.contains('aw-nav-open')) {
      const items = [...sidebar.querySelectorAll('a, input, button')].filter(el => el.getClientRects().length);
      if (e.shiftKey && document.activeElement === items[0]) { e.preventDefault(); items.at(-1).focus(); }
      else if (!e.shiftKey && document.activeElement === items.at(-1)) { e.preventDefault(); items[0].focus(); }
    }
  });
  const media = matchMedia('(max-width: 1100px)');
  media.addEventListener('change', () => setOpen(false));
  setOpen(false);
  if (document.body.classList.contains('admin-panel-workspace')) {
    const navigate = () => {
      const id = location.hash.slice(1) || 'dashboard';
      if (document.getElementById('section-' + id)) showSection(id);
    };
    sidebar.querySelectorAll('.aw-link').forEach(link => link.addEventListener('click', e => {
      const url = new URL(link.href);
      if (url.pathname === '/admin-panel' && url.hash && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey) {
        e.preventDefault(); if (location.hash === url.hash) navigate(); else location.hash = url.hash;
        setOpen(false);
      }
    }));
    window.addEventListener('hashchange', navigate);
    if (location.hash) navigate();
  }
})();
