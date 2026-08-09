const KEY = document.body.dataset.adminKey;
const PREVIEW_MODE = document.body.dataset.previewMode === 'true';
const DAY_NAMES = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
const TYPE_NAMES = {weekly:'Hebdomadaire', rotation:'Rotation 4 semaines', once:'Envoi unique'};
const POST_TYPE_NAMES = {message:'Message / Photo', quiz:'Quiz', poll:'Sondage'};

let posts = [];
let channels = [];
let currentImageUrl = '';
let toastTimer = null;
let selectedCsvFile = null;
let previewHistory = [
  {name:'Le rituel du lundi',post_kind:'custom-editorial',status:'sent',content:'Une semaine de trading commence par un plan.',sent_at:new Date(Date.now()-86400000).toISOString()},
  {name:'Calendrier économique',post_kind:'economic-calendar',status:'sent',content:'Annonces économiques du jour',sent_at:new Date(Date.now()-3600000*5).toISOString()},
  {name:'Rappel gestion du risque',post_kind:'custom-editorial',status:'sent',content:'Protège ton capital.',sent_at:new Date(Date.now()-3600000).toISOString()}
];

const $ = (id) => document.getElementById(id);
const escapeHtml = (value='') => String(value).replace(/[&<>'"]/g, char => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'
})[char]);

function currentPostType() {
  return document.querySelector('input[name="post-type"]:checked')?.value || 'message';
}

function pollOptionRows() {
  return [...document.querySelectorAll('.poll-option-row')];
}

function pollOptions() {
  return pollOptionRows().map(row => row.querySelector('.poll-option-input').value.trim());
}

function pollCorrectOptionIds() {
  return pollOptionRows().flatMap((row, index) => row.querySelector('.poll-correct').checked ? [index] : []);
}

function renderPollOptionEditor(options=['','','',''], correctIds=[]) {
  const normalized = options.length >= 2 ? options : [...options, ...Array(2 - options.length).fill('')];
  $('poll-options-editor').innerHTML = normalized.map((option, index) => `
    <div class="poll-option-row">
      <span>${index + 1}</span>
      <input class="poll-option-input" maxlength="100" value="${escapeHtml(option)}" placeholder="Réponse ${index + 1}">
      <label class="poll-correct-label" title="Marquer comme bonne réponse">
        <input class="poll-correct" type="checkbox" ${correctIds.includes(index) ? 'checked' : ''}>
        <i>✓</i><small>Bonne</small>
      </label>
      <button class="remove-poll-option" type="button" aria-label="Supprimer cette réponse" ${normalized.length <= 2 ? 'disabled' : ''}>×</button>
    </div>`).join('');
  $('add-poll-option').disabled = normalized.length >= 12;
  updatePollEditorMode();
}

function updatePollEditorMode() {
  const isQuiz = currentPostType() === 'quiz';
  $('quiz-explanation-field').hidden = !isQuiz;
  $('correct-answer-help').hidden = !isQuiz;
  document.querySelectorAll('.poll-correct-label').forEach(label => { label.hidden = !isQuiz; });
  if (!isQuiz) document.querySelectorAll('.poll-correct').forEach(input => { input.checked = false; });
}

function showToast(message, type='success') {
  const toast = $('toast');
  toast.textContent = message;
  toast.className = `toast ${type} show`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3400);
}

function selectedChannelIds() {
  return [...document.querySelectorAll('#channel-targets input:checked')].map(input => Number(input.value));
}

function renderChannelTargets(selectedIds=null) {
  const preserved = selectedIds ?? selectedChannelIds();
  const activeChannels = channels.filter(channel => channel.active);
  if (!activeChannels.length) {
    $('channel-targets').innerHTML = '<div class="channel-target-empty">Aucun canal actif. Ajoute ou réactive un canal.</div>';
    return;
  }
  $('channel-targets').innerHTML = activeChannels.map(channel => `
    <label><input type="checkbox" value="${channel.id}" ${preserved.includes(Number(channel.id)) ? 'checked' : ''}><span>✓</span><div><strong>${escapeHtml(channel.name)}</strong><small>${escapeHtml(channel.chat_id)}</small></div></label>
  `).join('');
}

function setBroadcastVisibility() {
  $('channel-targets').hidden = $('publish-all-channels').checked;
}

function renderChannels() {
  if (!channels.length) {
    $('channels-list').innerHTML = '<div class="empty-state">Aucun canal connecté pour le moment.</div>';
    renderChannelTargets([]);
    return;
  }
  $('channels-list').innerHTML = channels.map(channel => {
    const status = channel.last_check_status || 'unchecked';
    const statusLabel = status === 'ready' ? 'Robot prêt' : status === 'permission_missing' ? 'Permission manquante' : status === 'error' ? 'À vérifier' : 'Non testé';
    return `<article class="channel-row">
      <span class="channel-avatar-small">✈</span>
      <div class="channel-main"><strong>${escapeHtml(channel.name)}</strong><small>${escapeHtml(channel.chat_id)}</small></div>
      <span class="channel-check-status ${escapeHtml(status)}">${escapeHtml(statusLabel)}</span>
      <span class="status-pill ${channel.active ? '' : 'draft'}">${channel.active ? 'Actif' : 'Suspendu'}</span>
      <div class="channel-actions">
        <button class="mini-action" type="button" title="Tester le robot" onclick="testChannel(${channel.id})">✓</button>
        <button class="mini-action" type="button" title="Modifier" onclick="editChannel(${channel.id})">✎</button>
        <button class="mini-action" type="button" title="${channel.active ? 'Suspendre' : 'Activer'}" onclick="toggleChannel(${channel.id},${!channel.active})">${channel.active ? 'Ⅱ' : '▶'}</button>
        <button class="mini-action delete" type="button" title="Retirer" onclick="deleteChannel(${channel.id})">×</button>
      </div>
    </article>`;
  }).join('');
  renderChannelTargets();
}

async function loadChannels() {
  if (PREVIEW_MODE) {
    if (!channels.length) channels = [{id:1,name:'Bectanse Académie',chat_id:'@BECTANSE_ACADEMIE',active:true,last_check_status:'ready'}];
    renderChannels();
    return;
  }
  try {
    const response = await fetch(`/admin/api/telegram/channels?key=${encodeURIComponent(KEY)}`);
    const data = await readJson(response);
    channels = data.channels;
    renderChannels();
  } catch (error) {
    $('channels-list').innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    showToast(error.message, 'error');
  }
}

function resetChannelForm() {
  $('channel-form').reset();
  $('channel-id').value = '';
  $('channel-active').checked = true;
  $('channel-form').hidden = true;
}

function editChannel(id) {
  const channel = channels.find(item => item.id === id);
  if (!channel) return;
  $('channel-id').value = channel.id;
  $('channel-name').value = channel.name;
  $('channel-chat-id').value = channel.chat_id;
  $('channel-active').checked = Boolean(channel.active);
  $('channel-form').hidden = false;
  $('channel-name').focus();
}

async function saveChannel(event) {
  event.preventDefault();
  const id = Number($('channel-id').value || 0);
  const payload = {key:KEY,...(id ? {id} : {}),name:$('channel-name').value.trim(),chat_id:$('channel-chat-id').value.trim(),active:$('channel-active').checked};
  if (!payload.name || !payload.chat_id) return showToast('Ajoute le nom et l’identifiant Telegram.', 'error');
  try {
    if (PREVIEW_MODE) {
      const normalized = {...payload,id:id || Math.max(0,...channels.map(channel => channel.id)) + 1,last_check_status:'unchecked'};
      channels = id ? channels.map(channel => channel.id === id ? normalized : channel) : [...channels, normalized];
    } else {
      const response = await fetch('/admin/api/telegram/channels/save', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      await readJson(response);
      await loadChannels();
    }
    resetChannelForm();
    renderChannels();
    showToast(id ? 'Canal mis à jour.' : 'Canal ajouté. Pense à tester le robot.');
  } catch (error) { showToast(error.message, 'error'); }
}

async function toggleChannel(id, active) {
  try {
    if (PREVIEW_MODE) channels = channels.map(channel => channel.id === id ? {...channel,active} : channel);
    else {
      const response = await fetch(`/admin/api/telegram/channels/${id}/toggle`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:KEY,active})});
      await readJson(response);
      await loadChannels();
    }
    renderChannels();
    showToast(active ? 'Canal activé.' : 'Canal suspendu.');
  } catch (error) { showToast(error.message, 'error'); }
}

async function testChannel(id) {
  try {
    if (PREVIEW_MODE) channels = channels.map(channel => channel.id === id ? {...channel,last_check_status:'ready'} : channel);
    else {
      const response = await fetch(`/admin/api/telegram/channels/${id}/test`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:KEY})});
      await readJson(response);
      await loadChannels();
    }
    renderChannels();
    showToast(PREVIEW_MODE ? 'Simulation : robot prêt à publier.' : 'Robot prêt à publier dans ce canal.');
  } catch (error) { showToast(error.message, 'error'); }
}

async function deleteChannel(id) {
  const channel = channels.find(item => item.id === id);
  if (!channel || !window.confirm(`Retirer « ${channel.name} » des destinations ?`)) return;
  try {
    if (PREVIEW_MODE) channels = channels.filter(item => item.id !== id);
    else {
      const response = await fetch(`/admin/api/telegram/channels/${id}/delete`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:KEY})});
      await readJson(response);
      await loadChannels();
    }
    renderChannels();
    showToast('Canal retiré des destinations.');
  } catch (error) { showToast(error.message, 'error'); }
}

async function readJson(response) {
  const data = await response.json().catch(() => ({ok:false,error:'Réponse serveur invalide'}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Une erreur est survenue');
  return data;
}

function telegramMarkup(text) {
  let safe = escapeHtml(text || 'Ton message apparaîtra ici…');
  safe = safe.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  safe = safe.replace(/\*([^*\n]+)\*/g, '<strong>$1</strong>');
  safe = safe.replace(/_([^_\n]+)_/g, '<em>$1</em>');
  return safe;
}

function setImage(url='') {
  currentImageUrl = url;
  const hasImage = Boolean(url);
  $('uploaded-image').hidden = !hasImage;
  $('upload-zone').hidden = hasImage;
  $('telegram-preview-image').hidden = !hasImage;
  if (hasImage) {
    $('uploaded-image-preview').src = url;
    $('telegram-preview-image').src = url;
  } else {
    $('uploaded-image-preview').removeAttribute('src');
    $('telegram-preview-image').removeAttribute('src');
    $('post-image').value = '';
  }
  updatePreview();
}

function selectedWeekdays() {
  return [...document.querySelectorAll('#days-picker input:checked')].map(input => Number(input.value));
}

function setScheduleVisibility() {
  const type = $('schedule-type').value;
  $('recurring-fields').hidden = type === 'once';
  $('once-field').hidden = type !== 'once';
  $('rotation-field').hidden = type !== 'rotation';
  updatePreview();
}

function setPostTypeVisibility() {
  const isMessage = currentPostType() === 'message';
  $('message-fields').hidden = !isMessage;
  $('poll-fields').hidden = isMessage;
  updatePollEditorMode();
  updatePreview();
}

function updatePreview() {
  const postType = currentPostType();
  const message = $('post-message').value;
  const max = currentImageUrl ? 1024 : 4096;
  $('char-count').textContent = `${message.length.toLocaleString('fr-FR')} / ${max.toLocaleString('fr-FR')}`;
  $('char-count').style.color = message.length > max ? '#fb7185' : '';
  $('telegram-preview-message').innerHTML = telegramMarkup(message);

  const question = $('poll-question').value.trim();
  const explanation = $('poll-explanation').value.trim();
  $('question-count').textContent = `${question.length.toLocaleString('fr-FR')} / 300`;
  $('explanation-count').textContent = `${explanation.length.toLocaleString('fr-FR')} / 200`;
  $('telegram-preview-standard').hidden = postType !== 'message';
  $('telegram-preview-poll').hidden = postType === 'message';
  if (postType !== 'message') {
    const options = pollOptions();
    $('telegram-poll-type').textContent = postType === 'quiz' ? 'QUIZ' : 'SONDAGE';
    $('telegram-poll-question').textContent = question || 'Ta question apparaîtra ici…';
    $('telegram-poll-options').innerHTML = options.map((option, index) => `
      <button type="button" data-option-index="${index}"><i></i><span>${escapeHtml(option || `Réponse ${index + 1}`)}</span><b></b></button>
    `).join('');
    $('telegram-poll-votes').textContent = postType === 'quiz'
      ? `Quiz ${$('poll-anonymous').checked ? 'anonyme' : 'public'}`
      : `Sondage ${$('poll-anonymous').checked ? 'anonyme' : 'public'}`;
    $('telegram-poll-bulb').hidden = postType !== 'quiz';
    $('telegram-poll-explanation').hidden = true;
    $('telegram-poll-explanation').textContent = explanation;
  }

  const buttonText = $('button-text').value.trim();
  $('telegram-preview-button').hidden = !buttonText;
  $('telegram-preview-button').textContent = buttonText || 'Découvrir';

  const type = $('schedule-type').value;
  let time = $('publish-time').value || '18:30';
  if (type === 'once' && $('scheduled-for').value) time = $('scheduled-for').value.slice(11,16);
  $('preview-time').textContent = time;
}

function previewPollVote(index) {
  const postType = currentPostType();
  const buttons = [...document.querySelectorAll('#telegram-poll-options button')];
  const selected = buttons[index];
  if (!selected) return;
  if (postType === 'quiz') {
    const correctIds = pollCorrectOptionIds();
    buttons.forEach((button, buttonIndex) => {
      button.classList.remove('selected', 'correct', 'wrong');
      if (correctIds.includes(buttonIndex)) button.classList.add('correct');
    });
    selected.classList.add('selected', correctIds.includes(index) ? 'correct' : 'wrong');
    selected.querySelector('b').textContent = correctIds.includes(index) ? '✓' : '×';
    const explanation = $('poll-explanation').value.trim();
    $('telegram-poll-explanation').hidden = !explanation;
  } else if ($('poll-multiple').checked) {
    selected.classList.toggle('selected');
  } else {
    buttons.forEach(button => button.classList.remove('selected'));
    selected.classList.add('selected');
  }
}

function resetForm() {
  $('post-form').reset();
  $('post-id').value = '';
  $('publish-time').value = '18:30';
  $('post-channel').value = '@BECTANSE_ACADEMIE';
  $('post-enabled').checked = true;
  $('publish-all-channels').checked = true;
  renderChannelTargets([]);
  setBroadcastVisibility();
  $('poll-anonymous').checked = true;
  renderPollOptionEditor(['','','',''], []);
  document.querySelectorAll('#days-picker input').forEach((input, index) => { input.checked = index === 0; });
  setImage('');
  setScheduleVisibility();
  setPostTypeVisibility();
  $('form-title').textContent = 'Nouvelle publication';
  $('save-label').textContent = 'Enregistrer le post';
  $('cancel-edit').hidden = true;
  updatePreview();
}

function formPayload() {
  const id = Number($('post-id').value || 0);
  const postType = currentPostType();
  return {
    key: KEY,
    ...(id ? {id} : {}),
    name: $('post-name').value.trim(),
    message: $('post-message').value.trim(),
    image_url: postType === 'message' ? currentImageUrl : '',
    post_type: postType,
    poll_question: $('poll-question').value.trim(),
    poll_options: pollOptions(),
    poll_correct_option_ids: pollCorrectOptionIds(),
    poll_explanation: $('poll-explanation').value.trim(),
    poll_anonymous: $('poll-anonymous').checked,
    poll_multiple: $('poll-multiple').checked,
    publish_all_channels: $('publish-all-channels').checked,
    channel_ids: selectedChannelIds(),
    schedule_type: $('schedule-type').value,
    weekdays: selectedWeekdays(),
    rotation_week: Number($('rotation-week').value),
    publish_time: $('publish-time').value,
    scheduled_for: $('scheduled-for').value,
    channel: $('post-channel').value.trim(),
    button_text: $('button-text').value.trim(),
    button_url: $('button-url').value.trim(),
    disable_notification: $('post-silent').checked,
    enabled: $('post-enabled').checked
  };
}

async function savePost(event) {
  event.preventDefault();
  const payload = formPayload();
  const max = currentImageUrl ? 1024 : 4096;
  if (!payload.name) return showToast('Ajoute un nom interne.', 'error');
  if (payload.post_type === 'message' && !payload.message) return showToast('Ajoute le message Telegram.', 'error');
  if (payload.post_type === 'message' && payload.message.length > max) return showToast(`Le message dépasse la limite de ${max} caractères.`, 'error');
  if (payload.post_type !== 'message' && !payload.poll_question) return showToast('Ajoute la question.', 'error');
  if (payload.post_type !== 'message' && (payload.poll_options.length < 2 || payload.poll_options.some(option => !option))) {
    return showToast('Renseigne au moins deux réponses sans laisser de ligne vide.', 'error');
  }
  if (payload.post_type === 'quiz' && !payload.poll_correct_option_ids.length) return showToast('Coche au moins une bonne réponse.', 'error');
  if ((payload.button_text && !payload.button_url) || (!payload.button_text && payload.button_url)) {
    return showToast('Renseigne le texte et le lien du bouton ensemble.', 'error');
  }
  if (!payload.publish_all_channels && !payload.channel_ids.length) return showToast('Choisis au moins un canal destinataire.', 'error');

  const submit = $('post-form').querySelector('[type="submit"]');
  submit.disabled = true;
  $('save-label').textContent = 'Enregistrement…';
  try {
    if (PREVIEW_MODE) {
      const existing = posts.find(post => post.id === payload.id);
      const normalized = {
        ...payload,
        id: payload.id || Math.max(0, ...posts.map(post => post.id)) + 1,
        next_run: new Date(Date.now() + 86400000).toISOString(),
        last_sent_at: existing?.last_sent_at || null
      };
      if (existing) posts = posts.map(post => post.id === payload.id ? normalized : post);
      else posts.unshift(normalized);
      showToast(existing ? 'Aperçu mis à jour.' : 'Post ajouté à l’aperçu.');
      resetForm();
      renderPosts();
      updateStats();
      return;
    }
    const response = await fetch('/admin/api/telegram/posts/save', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    await readJson(response);
    showToast(payload.id ? 'Publication mise à jour.' : 'Publication ajoutée au planning.');
    resetForm();
    await Promise.all([loadPosts(), loadHistory()]);
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    submit.disabled = false;
    $('save-label').textContent = $('post-id').value ? 'Mettre à jour' : 'Enregistrer le post';
  }
}

async function uploadImage(file) {
  if (!file) return;
  const allowed = ['image/jpeg','image/png','image/webp','image/gif'];
  if (!allowed.includes(file.type)) return showToast('Utilise une image JPG, PNG, WebP ou GIF.', 'error');
  if (file.size > 8 * 1024 * 1024) return showToast('L’image dépasse 8 Mo.', 'error');
  if (PREVIEW_MODE) {
    setImage(URL.createObjectURL(file));
    showToast('Image ajoutée à l’aperçu.');
    return;
  }
  const form = new FormData();
  form.append('key', KEY);
  form.append('image', file);
  $('upload-zone').querySelector('strong').textContent = 'Import en cours…';
  try {
    const response = await fetch('/admin/api/telegram/upload', {method:'POST', body:form});
    const data = await readJson(response);
    setImage(data.url);
    showToast('Image ajoutée au post.');
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    $('upload-zone').querySelector('strong').textContent = 'Ajouter une image';
  }
}

function scheduleLabel(post) {
  if (post.schedule_type === 'once') {
    return post.scheduled_for ? new Intl.DateTimeFormat('fr-FR', {dateStyle:'medium',timeStyle:'short'}).format(new Date(post.scheduled_for)) : 'Date non définie';
  }
  const days = (post.weekdays || []).map(day => DAY_NAMES[day]).join(', ');
  const rotation = post.schedule_type === 'rotation' ? ` · S${Number(post.rotation_week) + 1}/4` : '';
  return `${days || '—'} · ${post.publish_time}${rotation}`;
}

function nextRunLabel(post) {
  if (!post.enabled) return 'Robot en pause';
  if (!post.next_run) return post.schedule_type === 'once' ? 'Échéance passée' : 'Aucun prochain envoi';
  return `Prochain : ${new Intl.DateTimeFormat('fr-FR', {weekday:'short',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(post.next_run))}`;
}

function renderPosts() {
  const query = $('post-search').value.trim().toLowerCase();
  const filter = $('post-filter').value;
  const filtered = posts.filter(post => {
    const searchable = `${post.name || ''} ${post.message || ''} ${post.poll_question || ''}`.toLowerCase();
    const matchesQuery = !query || searchable.includes(query);
    const matchesFilter = filter === 'all' || (filter === 'active' ? post.enabled : !post.enabled);
    return matchesQuery && matchesFilter;
  }).sort((firstPost, secondPost) => {
    const firstRun = firstPost.next_run ? new Date(firstPost.next_run).getTime() : Number.MAX_SAFE_INTEGER;
    const secondRun = secondPost.next_run ? new Date(secondPost.next_run).getTime() : Number.MAX_SAFE_INTEGER;
    return firstRun - secondRun || Number(firstPost.id) - Number(secondPost.id);
  });
  if (!filtered.length) {
    $('posts-list').innerHTML = '<div class="empty-state">Aucune publication ne correspond à ce filtre.</div>';
    return;
  }
  $('posts-list').innerHTML = filtered.map(post => `
    <article class="post-row">
      <div class="post-thumb ${escapeHtml(post.post_type || 'message')}">${post.image_url ? `<img src="${escapeHtml(post.image_url)}" alt="">` : post.post_type === 'quiz' ? '?' : post.post_type === 'poll' ? '◉' : '✈'}</div>
      <div class="post-main">
        <div class="post-title"><strong>${escapeHtml(post.name)}</strong><span class="status-pill ${post.enabled ? '' : 'draft'}">${post.enabled ? 'Actif' : 'Brouillon'}</span></div>
        <div class="post-excerpt"><b>${escapeHtml(POST_TYPE_NAMES[post.post_type || 'message'])}</b> · ${escapeHtml((post.post_type === 'message' ? post.message : post.poll_question || post.message).replace(/\n/g,' '))}</div>
      </div>
      <div class="post-schedule"><strong>${escapeHtml(TYPE_NAMES[post.schedule_type] || post.schedule_type)} · ${escapeHtml(scheduleLabel(post))}</strong><small>${escapeHtml(nextRunLabel(post))}</small></div>
      <div class="post-actions">
        <button class="mini-action" type="button" title="Modifier" onclick="editPost(${post.id})">✎</button>
        <button class="mini-action" type="button" title="${post.enabled ? 'Mettre en pause' : 'Activer'}" onclick="togglePost(${post.id},${!post.enabled})">${post.enabled ? 'Ⅱ' : '▶'}</button>
        <button class="mini-action send" type="button" title="Envoyer maintenant" onclick="sendNow(${post.id})">➤</button>
        <button class="mini-action delete" type="button" title="Supprimer" onclick="deletePost(${post.id})">×</button>
      </div>
    </article>`).join('');
}

async function loadPosts() {
  if (PREVIEW_MODE) {
    if (!posts.length) {
      const tomorrow = new Date(Date.now() + 86400000).toISOString();
      const inThreeDays = new Date(Date.now() + 3 * 86400000).toISOString();
      posts = [
        {id:1,name:'Le rituel du lundi',post_type:'message',message:'🔥 *LE RITUEL DU LUNDI*\n\nPrépare ton risque avant d’ouvrir une position.',image_url:'',schedule_type:'weekly',weekdays:[0],rotation_week:null,publish_time:'18:30',scheduled_for:null,channel:'@BECTANSE_ACADEMIE',publish_all_channels:true,channel_ids:[],button_text:'',button_url:'',disable_notification:false,enabled:true,next_run:tomorrow,last_sent_at:null},
        {id:2,name:'Débrief du vendredi',post_type:'message',message:'📊 *LE DÉBRIEF DU VENDREDI*\n\nNote ta meilleure décision et l’erreur à ne pas répéter.',image_url:'',schedule_type:'weekly',weekdays:[4],rotation_week:null,publish_time:'18:00',scheduled_for:null,channel:'@BECTANSE_ACADEMIE',button_text:'Ouvrir mon espace',button_url:'https://acces.bectanse-academie.com',disable_notification:false,enabled:true,next_run:inThreeDays,last_sent_at:null},
        {id:3,name:'Quiz gestion du risque',post_type:'quiz',message:'Quel ratio signifie que le gain potentiel est deux fois supérieur au risque ?',poll_question:'Quel ratio signifie que le gain potentiel est deux fois supérieur au risque ?',poll_options:['1:1','1:2','2:1','3:1'],poll_correct_option_ids:[1],poll_explanation:'Le ratio 1:2 vise deux unités de gain pour une unité risquée.',poll_anonymous:true,poll_multiple:false,image_url:'',schedule_type:'rotation',weekdays:[6],rotation_week:1,publish_time:'11:00',scheduled_for:null,channel:'@BECTANSE_ACADEMIE',button_text:'',button_url:'',disable_notification:true,enabled:true,next_run:inThreeDays,last_sent_at:null},
        {id:4,name:'Sondage de la communauté',post_type:'poll',message:'Quel sujet veux-tu travailler ?',poll_question:'Quel sujet veux-tu travailler cette semaine ?',poll_options:['Gestion du risque','Psychologie','Lecture du marché'],poll_correct_option_ids:[],poll_explanation:'',poll_anonymous:true,poll_multiple:false,image_url:'',schedule_type:'once',weekdays:[],rotation_week:null,publish_time:'18:30',scheduled_for:tomorrow.slice(0,16),channel:'@BECTANSE_ACADEMIE',button_text:'',button_url:'',disable_notification:false,enabled:false,next_run:null,last_sent_at:null}
      ];
    }
    updateStats();
    renderPosts();
    return;
  }
  try {
    const response = await fetch(`/admin/api/telegram/posts?key=${encodeURIComponent(KEY)}`);
    const data = await readJson(response);
    posts = data.posts;
    $('stat-total').textContent = data.stats.total;
    $('stat-active').textContent = data.stats.active;
    $('stat-scheduled').textContent = data.stats.scheduled;
    $('stat-interactive').textContent = data.stats.interactive ?? data.posts.filter(post => post.post_type === 'quiz' || post.post_type === 'poll').length;
    renderPosts();
  } catch (error) {
    $('posts-list').innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    showToast(error.message, 'error');
  }
}

function updateStats() {
  $('stat-total').textContent = posts.length;
  $('stat-active').textContent = posts.filter(post => post.enabled).length;
  $('stat-scheduled').textContent = posts.filter(post => post.next_run).length;
  $('stat-interactive').textContent = posts.filter(post => post.post_type === 'quiz' || post.post_type === 'poll').length;
}

function editPost(id) {
  const post = posts.find(item => item.id === id);
  if (!post) return;
  $('post-id').value = post.id;
  $('post-name').value = post.name;
  $('post-message').value = (post.post_type || 'message') === 'message' ? post.message : '';
  const postTypeInput = document.querySelector(`input[name="post-type"][value="${post.post_type || 'message'}"]`);
  if (postTypeInput) postTypeInput.checked = true;
  $('poll-question').value = post.poll_question || '';
  $('poll-explanation').value = post.poll_explanation || '';
  $('poll-anonymous').checked = post.poll_anonymous !== false;
  $('poll-multiple').checked = Boolean(post.poll_multiple);
  renderPollOptionEditor(post.poll_options || ['','','',''], post.poll_correct_option_ids || []);
  $('schedule-type').value = post.schedule_type;
  $('publish-time').value = post.publish_time || '18:30';
  $('rotation-week').value = String(post.rotation_week ?? 0);
  $('scheduled-for').value = post.scheduled_for ? post.scheduled_for.slice(0,16) : '';
  $('post-channel').value = post.channel || '@BECTANSE_ACADEMIE';
  $('publish-all-channels').checked = post.publish_all_channels !== false;
  renderChannelTargets(post.channel_ids || []);
  setBroadcastVisibility();
  $('button-text').value = post.button_text || '';
  $('button-url').value = post.button_url || '';
  $('post-silent').checked = Boolean(post.disable_notification);
  $('post-enabled').checked = Boolean(post.enabled);
  document.querySelectorAll('#days-picker input').forEach(input => { input.checked = (post.weekdays || []).includes(Number(input.value)); });
  setImage(post.image_url || '');
  setScheduleVisibility();
  setPostTypeVisibility();
  $('form-title').textContent = 'Modifier la publication';
  $('save-label').textContent = 'Mettre à jour';
  $('cancel-edit').hidden = false;
  window.scrollTo({top:0,behavior:'smooth'});
}

async function togglePost(id, enabled) {
  if (PREVIEW_MODE) {
    posts = posts.map(post => post.id === id ? {...post,enabled} : post);
    showToast(enabled ? 'Robot activé dans l’aperçu.' : 'Robot mis en pause dans l’aperçu.');
    updateStats();
    renderPosts();
    return;
  }
  try {
    const response = await fetch(`/admin/api/telegram/posts/${id}/toggle`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({key:KEY,enabled})
    });
    await readJson(response);
    showToast(enabled ? 'Robot activé.' : 'Robot mis en pause.');
    await loadPosts();
  } catch (error) { showToast(error.message, 'error'); }
}

async function sendNow(id) {
  const post = posts.find(item => item.id === id);
  const targetCount = post?.publish_all_channels !== false ? channels.filter(channel => channel.active).length : (post?.channel_ids || []).length;
  if (!post || !window.confirm(`Envoyer « ${post.name} » maintenant sur ${targetCount} canal${targetCount > 1 ? 'aux' : ''} ?`)) return;
  if (PREVIEW_MODE) {
    previewHistory.unshift({name:post.name,post_kind:'manual-editorial',status:'sent',content:post.message,sent_at:new Date().toISOString()});
    showToast('Simulation réussie : aucun message réel n’a été envoyé.');
    renderHistory(previewHistory);
    return;
  }
  try {
    const response = await fetch(`/admin/api/telegram/posts/${id}/send-now`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({key:KEY})
    });
    const data = await readJson(response);
    const delivery = data.delivery || {};
    showToast(`${delivery.sent || targetCount} canal${(delivery.sent || targetCount) > 1 ? 'aux' : ''} confirmé${(delivery.sent || targetCount) > 1 ? 's' : ''}.`);
    await Promise.all([loadPosts(), loadHistory()]);
  } catch (error) { showToast(error.message, 'error'); }
}

async function deletePost(id) {
  const post = posts.find(item => item.id === id);
  if (!post || !window.confirm(`Supprimer « ${post.name} » du planning ?`)) return;
  if (PREVIEW_MODE) {
    posts = posts.filter(item => item.id !== id);
    showToast('Publication retirée de l’aperçu.');
    updateStats();
    renderPosts();
    return;
  }
  try {
    const response = await fetch(`/admin/api/telegram/posts/${id}/delete`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({key:KEY})
    });
    await readJson(response);
    showToast('Publication retirée du planning.');
    if (Number($('post-id').value) === id) resetForm();
    await loadPosts();
  } catch (error) { showToast(error.message, 'error'); }
}

function renderHistory(history) {
    if (!history.length) {
      $('history-list').innerHTML = '<div class="empty-state">Aucun envoi enregistré pour le moment.</div>';
      return;
    }
    $('history-list').innerHTML = history.map(item => {
      const date = item.sent_at || item.created_at;
      const dateLabel = date ? new Intl.DateTimeFormat('fr-FR', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(date)) : '—';
      return `<div class="history-row">
        <span class="history-date">${escapeHtml(dateLabel)}</span>
        <span class="history-name">${escapeHtml(item.name || item.content.slice(0,80) || item.slot_key)}</span>
        <span class="history-kind">${escapeHtml(item.target_channel || item.post_kind)}</span>
        <span class="history-status ${escapeHtml(item.status)}">${escapeHtml(item.status === 'sent' ? 'Envoyé' : item.status === 'failed' ? 'Échec' : 'En cours')}</span>
      </div>`;
    }).join('');
}

async function loadHistory() {
  if (PREVIEW_MODE) {
    renderHistory(previewHistory);
    return;
  }
  try {
    const response = await fetch(`/admin/api/telegram/history?key=${encodeURIComponent(KEY)}`);
    const data = await readJson(response);
    renderHistory(data.history);
  } catch (error) {
    $('history-list').innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function csvSummaryMarkup(summary) {
  return `<strong>Fichier prêt · ${summary.total} publication${summary.total > 1 ? 's' : ''}</strong>
    <span>${summary.messages} message${summary.messages > 1 ? 's' : ''} · ${summary.quizzes} quiz · ${summary.polls} sondage${summary.polls > 1 ? 's' : ''}</span>`;
}

async function validateCsv(file) {
  if (!file) return;
  selectedCsvFile = null;
  $('import-csv').disabled = true;
  $('csv-validation').hidden = false;
  $('csv-validation').className = 'csv-validation loading';
  $('csv-validation').innerHTML = '<strong>Contrôle du fichier…</strong><span>Dates, heures, formats et réponses sont vérifiés.</span>';
  const form = new FormData();
  form.append('key', KEY);
  form.append('file', file);
  form.append('dry_run', 'true');
  try {
    const response = await fetch('/admin/api/telegram/csv/import', {method:'POST', body:form});
    const data = await response.json().catch(() => ({ok:false,error:'Réponse serveur invalide'}));
    if (!response.ok || !data.ok) {
      const details = (data.errors || []).slice(0,5).map(error => `Ligne ${error.line} : ${escapeHtml(error.error)}`).join('<br>');
      throw new Error(`${data.error || 'Fichier invalide'}${details ? `<br>${details}` : ''}`);
    }
    selectedCsvFile = file;
    $('csv-validation').className = 'csv-validation valid';
    $('csv-validation').innerHTML = csvSummaryMarkup(data.summary);
    $('import-csv').disabled = false;
  } catch (error) {
    $('csv-validation').className = 'csv-validation invalid';
    $('csv-validation').innerHTML = `<strong>Le fichier doit être corrigé</strong><span>${error.message}</span>`;
  }
}

async function importCsv() {
  if (!selectedCsvFile) return;
  const button = $('import-csv');
  button.disabled = true;
  button.textContent = 'Import en cours…';
  const form = new FormData();
  form.append('key', KEY);
  form.append('file', selectedCsvFile);
  form.append('dry_run', 'false');
  try {
    const response = await fetch('/admin/api/telegram/csv/import', {method:'POST', body:form});
    const data = await readJson(response);
    if (data.preview_mode) {
      showToast(`Simulation réussie : ${data.summary.total} publications validées, sans envoi.`);
    } else {
      const duplicateText = data.duplicates ? ` · ${data.duplicates} doublon(s) ignoré(s)` : '';
      showToast(`${data.imported} publications ajoutées${duplicateText}.`);
      await loadPosts();
    }
    selectedCsvFile = null;
    $('csv-file').value = '';
    $('csv-validation').hidden = true;
  } catch (error) {
    showToast(error.message, 'error');
    button.disabled = false;
  } finally {
    button.textContent = 'Importer les publications →';
  }
}

function csvEscape(value='') {
  const text = String(value ?? '');
  return /[;"\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function exportPreviewCsv() {
  const headers = ['nom','type','date','heure','rythme','jours','semaine_rotation','canal','message','image_url','texte_bouton','lien_bouton','question','reponses','bonnes_reponses','explication','anonyme','choix_multiples','silencieux','actif','tous_les_canaux','canaux'];
  const typeLabels = {message:'message',quiz:'quiz',poll:'sondage'};
  const rhythmLabels = {weekly:'hebdomadaire',rotation:'rotation',once:'unique'};
  const dayLabels = ['lun','mar','mer','jeu','ven','sam','dim'];
  const rows = posts.map(post => {
    const scheduled = post.scheduled_for ? new Date(post.scheduled_for) : null;
    const date = scheduled && !Number.isNaN(scheduled.valueOf()) ? new Intl.DateTimeFormat('fr-FR').format(scheduled) : '';
    const hour = scheduled && !Number.isNaN(scheduled.valueOf()) ? scheduled.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'}) : post.publish_time || '';
    return [
      post.name, typeLabels[post.post_type || 'message'], date, hour, rhythmLabels[post.schedule_type],
      (post.weekdays || []).map(day => dayLabels[day]).join('|'), post.rotation_week == null ? '' : Number(post.rotation_week) + 1,
      post.channel, (post.post_type || 'message') === 'message' ? post.message : '', post.image_url || '', post.button_text || '', post.button_url || '',
      post.poll_question || '', (post.poll_options || []).join('|'), (post.poll_correct_option_ids || []).map(index => Number(index) + 1).join('|'),
      post.poll_explanation || '', post.poll_anonymous === false ? 'non' : 'oui', post.poll_multiple ? 'oui' : 'non',
      post.disable_notification ? 'oui' : 'non', post.enabled ? 'oui' : 'non',
      post.publish_all_channels === false ? 'non' : 'oui',
      (post.channel_ids || []).map(id => channels.find(channel => channel.id === id)?.chat_id || '').filter(Boolean).join('|')
    ];
  });
  const content = '\ufeff' + [headers, ...rows].map(row => row.map(csvEscape).join(';')).join('\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([content], {type:'text/csv;charset=utf-8'}));
  link.download = 'planning-telegram-apercu.csv';
  link.click();
  URL.revokeObjectURL(link.href);
}

$('post-form').addEventListener('submit', savePost);
$('reset-form').addEventListener('click', resetForm);
$('cancel-edit').addEventListener('click', resetForm);
$('schedule-type').addEventListener('change', setScheduleVisibility);
$('publish-all-channels').addEventListener('change', setBroadcastVisibility);
$('channel-form').addEventListener('submit', saveChannel);
$('add-channel').addEventListener('click', () => { resetChannelForm(); $('channel-form').hidden = false; $('channel-name').focus(); });
$('cancel-channel').addEventListener('click', resetChannelForm);
document.querySelectorAll('input[name="post-type"]').forEach(input => input.addEventListener('change', setPostTypeVisibility));
$('post-message').addEventListener('input', updatePreview);
$('poll-question').addEventListener('input', updatePreview);
$('poll-explanation').addEventListener('input', updatePreview);
$('poll-anonymous').addEventListener('change', updatePreview);
$('poll-multiple').addEventListener('change', updatePreview);
$('poll-options-editor').addEventListener('input', updatePreview);
$('poll-options-editor').addEventListener('change', updatePreview);
$('poll-options-editor').addEventListener('click', event => {
  const removeButton = event.target.closest('.remove-poll-option');
  if (!removeButton || removeButton.disabled) return;
  removeButton.closest('.poll-option-row').remove();
  pollOptionRows().forEach((row, index) => {
    row.querySelector(':scope > span').textContent = index + 1;
    row.querySelector('.poll-option-input').placeholder = `Réponse ${index + 1}`;
    row.querySelector('.remove-poll-option').disabled = pollOptionRows().length <= 2;
  });
  $('add-poll-option').disabled = false;
  updatePreview();
});
$('add-poll-option').addEventListener('click', () => {
  const options = pollOptions();
  if (options.length >= 12) return;
  renderPollOptionEditor([...options, ''], pollCorrectOptionIds());
  pollOptionRows().at(-1)?.querySelector('.poll-option-input').focus();
  updatePreview();
});
$('button-text').addEventListener('input', updatePreview);
$('button-url').addEventListener('input', updatePreview);
$('publish-time').addEventListener('input', updatePreview);
$('scheduled-for').addEventListener('input', updatePreview);
$('post-image').addEventListener('change', event => uploadImage(event.target.files[0]));
$('remove-image').addEventListener('click', () => setImage(''));
$('post-search').addEventListener('input', renderPosts);
$('post-filter').addEventListener('change', renderPosts);
$('refresh-posts').addEventListener('click', loadPosts);
$('refresh-history').addEventListener('click', loadHistory);
$('telegram-preview-button').addEventListener('click', event => event.preventDefault());
$('telegram-poll-options').addEventListener('click', event => {
  const button = event.target.closest('button[data-option-index]');
  if (button) previewPollVote(Number(button.dataset.optionIndex));
});
$('csv-file').addEventListener('change', event => validateCsv(event.target.files[0]));
$('import-csv').addEventListener('click', importCsv);
$('export-csv').addEventListener('click', event => {
  if (!PREVIEW_MODE) return;
  event.preventDefault();
  exportPreviewCsv();
});

const uploadZone = $('upload-zone');
['dragenter','dragover'].forEach(type => uploadZone.addEventListener(type, event => { event.preventDefault(); uploadZone.classList.add('dragging'); }));
['dragleave','drop'].forEach(type => uploadZone.addEventListener(type, event => { event.preventDefault(); uploadZone.classList.remove('dragging'); }));
uploadZone.addEventListener('drop', event => uploadImage(event.dataTransfer.files[0]));

const csvDropzone = $('csv-dropzone');
['dragenter','dragover'].forEach(type => csvDropzone.addEventListener(type, event => { event.preventDefault(); csvDropzone.classList.add('dragging'); }));
['dragleave','drop'].forEach(type => csvDropzone.addEventListener(type, event => { event.preventDefault(); csvDropzone.classList.remove('dragging'); }));
csvDropzone.addEventListener('drop', event => {
  const file = event.dataTransfer.files[0];
  if (file) validateCsv(file);
});

async function initializeStudio() {
  resetForm();
  await loadChannels();
  await Promise.all([loadPosts(), loadHistory()]);
}
initializeStudio();

window.editPost = editPost;
window.togglePost = togglePost;
window.sendNow = sendNow;
window.deletePost = deletePost;
window.editChannel = editChannel;
window.toggleChannel = toggleChannel;
window.testChannel = testChannel;
window.deleteChannel = deleteChannel;
