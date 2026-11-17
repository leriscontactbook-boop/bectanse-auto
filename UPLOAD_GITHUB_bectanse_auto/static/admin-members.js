/* Member directory presentation. Actions call the existing administration flows. */
const adminMemberLists = {};
function adminEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function renderAdminMemberList(kind, members, page = 1, meta = null) {
  const size = 15;
  const total = meta ? meta.total : members.length;
  const pages = meta ? meta.pages : Math.max(1, Math.ceil(total / size));
  page = Math.max(1, Math.min(pages, page));
  adminMemberLists[kind] = {members, page, meta};
  const body = document.getElementById(kind === 'dashboard' ? 'dashboard-membres-tbody' : 'membres-tbody');
  const start = (page - 1) * size;
  body.innerHTML = (meta ? members : members.slice(start, start + size)).map((m, index) => {
    const explorer = m.access_level === 'explorer';
    const active = !explorer && m.actif && !m.est_expire;
    const status = explorer ? 'Explorer' : m.est_expire ? 'Expiré' : m.actif ? 'Actif' : 'Suspendu';
    const initials = String(m.nom || m.code || '?').trim().split(/\s+/).slice(0,2).map(x => Array.from(x)[0]).join('').toUpperCase();
    const days = m.jours_restants == null ? NaN : Number(m.jours_restants);
    return `<tr><td><div class="aw-member"><span class="aw-avatar" aria-hidden="true">${adminEscape(initials)}</span><div><span class="aw-member-name">${adminEscape(m.nom || 'Sans nom')}</span><span class="aw-member-code">${adminEscape(m.code)}</span></div></div></td>
      <td>${m.capital == null || m.capital === '' ? '—' : adminEscape(Number.isFinite(Number(m.capital)) ? Number(m.capital).toLocaleString('fr-FR') : m.capital)}</td><td><span class="badge ${explorer ? 'warn' : active ? 'on' : 'off'}">${status}</span></td>
      <td>${m.copy_actif ? '<span class="badge on">Activé</span>' : '<span class="aw-copy-off">Désactivé</span>'}</td>
      <td>${adminEscape(m.date_fin || '—')}</td><td>${explorer || !Number.isFinite(days) ? '—' : `<span class="badge ${days > 7 ? 'on' : days > 0 ? 'warn' : 'off'}">${days > 0 ? days + ' j' : 'Échu'}</span>`}</td>
      <td><div class="aw-row-actions"><button type="button" class="btn btn-sm aw-secondary" data-member-list="${kind}" data-member-index="${meta ? index : start + index}" data-member-command="profile">Profil</button><button type="button" class="btn btn-sm aw-secondary" data-member-list="${kind}" data-member-index="${meta ? index : start + index}" data-member-command="more" aria-label="Actions pour ${adminEscape(m.nom || m.code)}">Gérer l’accès</button></div></td></tr>`;
  }).join('') || '<tr><td colspan="7" class="aw-empty"><strong>Aucun membre à afficher</strong>Essayez un autre nom, un autre code ou un autre filtre.</td></tr>';
  document.getElementById(kind + '-pagination').innerHTML = `<span aria-live="polite">${total ? start + 1 : 0}–${Math.min(start + size,total)} sur ${total} membres</span><div class="aw-pagination"><span>Page ${page} / ${pages}</span><button type="button" data-list-page="${kind}" data-page="${page-1}" ${page===1?'disabled':''} aria-label="Page précédente"><svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 6l-6 6 6 6"/></svg></button><button type="button" data-list-page="${kind}" data-page="${page+1}" ${page===pages?'disabled':''} aria-label="Page suivante"><svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6l6 6-6 6"/></svg></button></div>`;
}
document.addEventListener('click', e => {
  const pager = e.target.closest('[data-list-page]');
  if (pager) {
    const {members, meta} = adminMemberLists[pager.dataset.listPage];
    if (meta) { loadAdminDirectory(pager.dataset.listPage, Number(pager.dataset.page)); return; }
    renderAdminMemberList(pager.dataset.listPage, members, Number(pager.dataset.page));
    document.getElementById(pager.dataset.listPage + '-pagination').querySelector('button:not(:disabled)')?.focus({preventScroll:true});
    return;
  }
  const button = e.target.closest('[data-member-command]');
  if (!button) return;
  const member = adminMemberLists[button.dataset.memberList]?.members[Number(button.dataset.memberIndex)];
  if (!member) return;
  if (button.dataset.memberCommand === 'profile') { voirProfil(member.code); return; }
  let dialog = document.getElementById('member-action-dialog');
  if (!dialog) {
    dialog = document.createElement('dialog'); dialog.id = 'member-action-dialog'; dialog.className = 'aw-action-dialog';
    dialog.setAttribute('aria-labelledby','member-action-title'); document.body.append(dialog);
  }
  dialog.innerHTML = '<h2 id="member-action-title">Actions du membre</h2><p></p><div class="aw-action-list"></div><button type="button" class="btn aw-secondary aw-dialog-close">Fermer</button>';
  dialog.querySelector('p').textContent = `${member.nom || 'Sans nom'} · ${member.code}`;
  const actions = [['Ouvrir le profil', () => voirProfil(member.code)]];
  actions.push(['Ajouter des jours', () => ajusterJours(member.code, 1)]);
  actions.push(['Retirer des jours', () => ajusterJours(member.code, -1)]);
  actions.push([member.actif ? 'Suspendre l’accès' : 'Réactiver l’accès', () => changerSuspension(member.code, Boolean(member.actif))]);
  actions.push(['Envoyer un message', () => msgMembre(member.code, member.nom)]);
  actions.push(['Supprimer le membre', () => supprimerMembre(member.code, member.nom), true]);
  for (const [label, action, destructive] of actions) {
    const item = document.createElement('button'); item.type = 'button'; item.className = 'btn ' + (destructive ? 'btn-red' : 'aw-secondary'); item.textContent = label;
    item.addEventListener('click', () => { dialog.close(); action(); }); dialog.querySelector('.aw-action-list').append(item);
  }
  dialog.querySelector('.aw-dialog-close').addEventListener('click', () => dialog.close());
  dialog.showModal();
});

const directoryRequests = {}, directoryTimers = {};
function scheduleDirectorySearch(kind) {
  clearTimeout(directoryTimers[kind]);
  directoryTimers[kind] = setTimeout(() => loadAdminDirectory(kind), 250);
}
async function loadAdminDirectory(kind, page = 1) {
  clearTimeout(directoryTimers[kind]);
  directoryRequests[kind]?.abort();
  const controller = new AbortController(); directoryRequests[kind] = controller;
  const search = document.getElementById(kind === 'dashboard' ? 'dashboard-search' : 'membre-search');
  const params = new URLSearchParams({paginated:'1',page:String(page),page_size:'15',q:search.value.trim(),scope:kind==='dashboard'?filtreDashboardActif:'all'});
  const activeLabels = [];
  document.querySelectorAll(`[data-directory-filters="${kind}"] select`).forEach(select => {
    params.set(select.dataset.filter, select.value);
    if (select.value && select.dataset.filter !== 'sort') activeLabels.push(select.selectedOptions[0].textContent);
  });
  if (search.value.trim()) activeLabels.unshift('Recherche : ' + search.value.trim());
  const body = document.getElementById(kind==='dashboard'?'dashboard-membres-tbody':'membres-tbody');
  const status = document.getElementById(kind+'-directory-status');
  body.setAttribute('aria-busy','true'); status.classList.remove('aw-error'); status.textContent = 'Actualisation des membres…';
  try {
    const response = await fetch(API('/admin/api/membres')+'&'+params, {signal:controller.signal});
    if (!response.ok) throw Error('unavailable');
    const data = await response.json();
    if (!data.ok || !Array.isArray(data.membres) || !Number.isInteger(data.total)) throw Error('invalid');
    renderAdminMemberList(kind,data.membres,data.page,data);
    status.textContent = activeLabels.length ? activeLabels.join(' · ') : '';
    if (kind==='dashboard') {
      document.getElementById('dashboard-filter-label').textContent = labelsFiltresDashboard[filtreDashboardActif];
      document.getElementById('dashboard-filter-count').textContent = `${data.total} profil${data.total>1?'s':''}`;
    }
  } catch (error) {
    if (error.name==='AbortError') return;
    status.classList.add('aw-error'); status.textContent = 'Impossible de charger les membres. Réessayez avec Actualiser.';
    if (!adminMemberLists[kind]) {
      body.innerHTML = '<tr><td colspan="7" class="aw-empty"><strong>Données indisponibles</strong>Vérifiez votre connexion, puis actualisez.</td></tr>';
      document.getElementById(kind+'-pagination').textContent = 'Chargement interrompu';
    } else status.textContent += ' Les derniers résultats restent affichés.';
  } finally {
    if (directoryRequests[kind]===controller) body.removeAttribute('aria-busy');
  }
}
document.addEventListener('change', e => {
  const filters = e.target.closest('[data-directory-filters]');
  if (filters) {
    if (filters.dataset.directoryFilters==='dashboard' && e.target.dataset.filter==='access') {
      if (e.target.value==='explorer') filtreDashboardActif='explorers';
      else if (filtreDashboardActif==='explorers') filtreDashboardActif='total';
      document.querySelectorAll('[data-dashboard-filter]').forEach(card=>{const selected=card.dataset.dashboardFilter===filtreDashboardActif;card.classList.toggle('active',selected);card.setAttribute('aria-pressed',String(selected));});
    }
    loadAdminDirectory(filters.dataset.directoryFilters);
  }
});
document.addEventListener('click', e => {
  const reset = e.target.closest('[data-reset-directory]');
  if (!reset) return;
  const kind = reset.dataset.resetDirectory;
  document.getElementById(kind==='dashboard'?'dashboard-search':'membre-search').value='';
  document.querySelectorAll(`[data-directory-filters="${kind}"] select`).forEach(select => select.selectedIndex=0);
  if (kind==='dashboard') appliquerFiltreDashboard('total'); else loadAdminDirectory(kind);
});

// Refresh an open directory after changes made from another admin session.
window.addEventListener('focus', () => {
  for (const kind of Object.keys(adminMemberLists)) loadAdminDirectory(kind, adminMemberLists[kind].page);
});
