(async () => {
  'use strict';
  const section = document.getElementById('temoignages');
  if (!section || section.dataset.horizontalFeed) return;
  const visualMount = section.querySelector('[data-member-feed="visuals"]');
  const audioMount = section.querySelector('[data-member-feed="audios"]');
  if (!visualMount && !audioMount) return;
  const audioOnly = !visualMount && Boolean(audioMount);
  const localTestimonials = '/static/temoignages';
  const publishedTestimonials = 'https://raw.githubusercontent.com/leriscontactbook-boop/bectanse-auto/codex/mt5-journal-production/UPLOAD_GITHUB_bectanse_auto/static/temoignages';
  const testimonialBase = location.hostname === 'acces.bectanse-academie.com' ? publishedTestimonials : localTestimonials;
  let catalog = window.__BECTANSE_TESTIMONIALS__ || null;
  if (!catalog) {
    try {
      const response = await fetch(`${testimonialBase}/temoignages.json?v=97b3374a9dc4`);
      if (!response.ok) return;
      catalog = await response.json();
    } catch (_) { return; }
  }
  if (testimonialBase !== localTestimonials) {
    catalog = JSON.parse(JSON.stringify(catalog).replaceAll(localTestimonials, testimonialBase));
  }
  if (!catalog.items?.length) return;
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const items = catalog.items;
  const byId = new Map(items.map(item => [item.id, item]));
  let modalTrigger;
  let previousBodyOverflow = '';
  let activePlayer = null;
  function pauseOthers(active) {
    [section, dialog].forEach(root => root.querySelectorAll('audio,video').forEach(media => {
      if (media !== active && !media.paused) media.pause();
    }));
    activePlayer = active || null;
  }
  function onTestimonialPlay(event) {
    if (event.target.matches('audio,video')) pauseOthers(event.target);
  }
  const duration = seconds => `${Math.floor(Math.round(seconds) / 60)}:${String(Math.round(seconds) % 60).padStart(2, '0')}`;
  function player(file, name) {
    const block = make('div', 'member-player');
    const heading = make('div', 'member-player-label');
    heading.append(make('span', '', 'La voix du membre'), make('span', '', 'Témoignage audio'));
    const audio = make('audio', 'member-feed-paired-audio');
    audio.preload = 'none'; audio.hidden = true;
    const controls = make('div', 'member-voice-controls');
    const play = make('button', 'member-voice-play'); play.type = 'button';
    const main = make('div', 'member-voice-main');
    const wave = make('div', 'member-voice-wave');
    const bars = make('div', 'member-voice-bars'); bars.setAttribute('aria-hidden', 'true');
    const seek = make('input', 'member-voice-seek');
    seek.type = 'range'; seek.min = '0'; seek.step = '0.1'; seek.value = '0';
    seek.setAttribute('aria-label', `Avancer dans le témoignage de ${name}`);
    const timing = make('div', 'member-voice-timing');
    const elapsed = make('span', '', '0:00'); const total = make('span');
    timing.append(elapsed, total); wave.append(bars, seek); main.append(wave, timing);
    controls.append(play, main);
    let currentFile = file, pendingSeek = null;
    const length = () => Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : currentFile.duration || 1;
    function refresh() {
      const playing = !audio.paused && !audio.ended;
      play.classList.toggle('is-playing', playing);
      play.setAttribute('aria-label', `${playing ? 'Mettre en pause' : 'Écouter'} le témoignage de ${name}`);
      play.setAttribute('aria-pressed', String(playing));
      seek.max = String(length()); seek.value = String(audio.currentTime || 0);
      seek.setAttribute('aria-valuetext', `${duration(audio.currentTime || 0)} sur ${duration(length())}`);
      elapsed.textContent = duration(audio.currentTime || 0); total.textContent = duration(length());
      [...bars.children].forEach((bar,index) => bar.classList.toggle('is-heard', index / bars.children.length < audio.currentTime / length()));
    }
    play.addEventListener('click', () => {
      if (!audio.paused) { audio.pause(); return; }
      pauseOthers(audio);
      audio.play().catch(reason => { if (reason.name !== 'AbortError') error.hidden = false; play.classList.remove('is-loading'); refresh(); });
    });
    seek.addEventListener('input', () => {
      pendingSeek = Number(seek.value);
      if (audio.readyState >= 1) { audio.currentTime = pendingSeek; pendingSeek = null; }
      else audio.load();
      elapsed.textContent = duration(Number(seek.value));
    });
    audio.addEventListener('loadedmetadata', () => {
      if (pendingSeek !== null) { audio.currentTime = pendingSeek; pendingSeek = null; }
      refresh();
    });
    ['play','pause','timeupdate','ended','durationchange'].forEach(event => audio.addEventListener(event, refresh));
    audio.addEventListener('waiting', () => play.classList.add('is-loading'));
    ['playing','canplay','pause'].forEach(event => audio.addEventListener(event, () => play.classList.remove('is-loading')));
    const error = make('button', 'member-media-error', 'Ce média ne se charge pas. Réessayer');
    error.hidden = true; error.type = 'button';
    audio.addEventListener('error', () => { error.hidden = false; });
    error.addEventListener('click', () => { audio.load(); error.hidden = true; });
    block.setFile = next => {
      audio.pause(); currentFile = next; audio.src = next.src; pendingSeek = null;
      bars.replaceChildren();
      (next.waveform || [20,40,60,80,60,40,20]).forEach(height => {
        const bar = make('i'); bar.style.height = `${height}%`; bars.append(bar);
      });
      error.hidden = true; refresh();
    };
    block.append(heading, controls, audio, error); block.setFile(file); return block;
  }
  function playlist(files, name) {
    const block = player(files[0], name);
    if (files.length > 1) {
      const select = make('select', 'member-audio-select');
      select.setAttribute('aria-label', `Choisir un vocal de ${name}`);
      files.forEach((file, index) => {
        const option = make('option', '', `Vocal ${index + 1} sur ${files.length} · ${duration(file.duration)}`);
        option.value = file.src; select.append(option);
      });
      select.addEventListener('change', () => {
        block.setFile(files[select.selectedIndex]);
      });
      block.insertBefore(select, block.querySelector('.member-voice-controls'));
    }
    return block;
  }
  function badge() { return null; }
  function details(item, audioOnly = false) {
    const box = make('div', 'member-card-details');
    const line = make('div', 'member-card-byline');
    line.append(make('h4', '', item.name));
    const seal = badge(item); if (seal) line.append(seal);
    box.append(line);
    if (item.result) {
      const result = make('div', 'member-result');
      result.append(make('strong', '', item.result.value), make('span', '', item.result.label));
      box.append(result);
    }
    if (item.context) box.append(make('p', 'member-context', item.context));
    if (item.quote && !audioOnly) box.append(make('blockquote', 'member-quote', item.quote));
    if (item.dateLabel) box.append(make('p', 'member-date', item.dateLabel));
    return box;
  }
  const dialog = make('dialog', 'member-feed-dialog');
  dialog.setAttribute('aria-label', 'Consulter le témoignage');
  const modalClose = make('button', 'member-feed-close', 'Fermer'); modalClose.type = 'button';
  const modalBody = make('div', 'member-modal-body'); dialog.append(modalClose, modalBody); document.body.append(dialog);
  section.addEventListener('play', onTestimonialPlay, true);
  dialog.addEventListener('play', onTestimonialPlay, true);
  modalClose.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    dialog.querySelectorAll('audio,video').forEach(p => p.pause());
    modalBody.replaceChildren(); document.body.style.overflow = previousBodyOverflow;
    if (modalTrigger?.isConnected) modalTrigger.focus({preventScroll:true});
  });
  function openImages(item, trigger, initial = 0) {
    pauseOthers(); modalTrigger = trigger; modalBody.replaceChildren();
    modalBody.append(details(item));
    const gallery = make('div', 'member-modal-gallery');
    const image = make('img');
    const navigation = make('div', 'member-modal-navigation');
    const previous = make('button', '', 'Image précédente');
    const next = make('button', '', 'Image suivante');
    previous.type = next.type = 'button';
    const counter = make('span'); let index = initial;
    function draw() {
      const file = item.images[index];
      image.src = file.src; image.alt = file.alt || `Résultat partagé par ${item.name}`;
      counter.textContent = `${index + 1} / ${item.images.length}`;
      previous.disabled = index === 0; next.disabled = index === item.images.length - 1;
    }
    previous.addEventListener('click', () => { index--; draw(); });
    next.addEventListener('click', () => { index++; draw(); });
    gallery.append(image); navigation.append(previous, counter, next);
    if (item.images?.length) {
      modalBody.append(gallery); if (item.images.length > 1) modalBody.append(navigation); draw();
    }
    (item.videos || []).forEach(file => {
      const video = make('video', 'member-modal-video'); video.src = file.src; video.poster = file.poster || '';
      video.controls = true; video.playsInline = true; video.preload = 'none'; modalBody.append(video);
    });
    if (item.audios?.length) modalBody.append(playlist(item.audios, item.name));
    const references = make('details', 'member-source-details');
    references.append(make('summary', '', 'Consulter les références sources'));
    item.sources?.forEach(source => {
      const link = make('a', '', source.messageId ? `${source.channel} · message ${source.messageId}` : 'Média conservé du site');
      link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; references.append(link);
    });
    modalBody.append(references);
    previousBodyOverflow = document.body.style.overflow;
    dialog.showModal(); document.body.style.overflow = 'hidden';
  }
  function visualCard(item) {
    const card = make('article', 'member-feed-item'); card.dataset.memberId = item.id;
    card.setAttribute('role', 'listitem');
    if (item.videos?.length) {
      card.classList.add('member-feed-video');
      const video = make('video', 'member-feed-inline-video');
      const file = item.videos[0]; video.src = file.src;
      if (file.poster) video.poster = file.poster;
      video.controls = true; video.playsInline = true; video.preload = 'none';
      video.setAttribute('aria-label', `Témoignage vidéo de ${item.name}`);
      video.addEventListener('play', () => pauseOthers(video));
      card.append(video);
    } else if (item.images?.length) {
      card.classList.add('member-feed-photo');
      const open = make('button', 'member-feed-photo-open'); open.type = 'button';
      open.setAttribute('aria-label', `Agrandir ${item.images.length > 1 ? 'les captures' : 'la capture'} de ${item.name}`);
      const image = make('img'); image.src = item.images[0].src;
      image.alt = item.images[0].alt || `Résultat partagé par ${item.name}`;
      image.loading = 'lazy'; image.decoding = 'async';
      open.append(image, make('span', 'member-feed-zoom', item.images.length > 1 ? `${item.images.length} captures · Agrandir` : 'Agrandir'));
      open.addEventListener('click', () => openImages(item, open)); card.append(open);
    } else {
      card.classList.add('member-feed-written');
      card.append(make('div', 'member-written-label', 'PAROLES DE MEMBRE'));
    }
    card.append(details(item));
    if (item.audios?.length) {
      card.classList.add('member-feed-paired');
      card.append(playlist(item.audios, item.name));
    }
    const more = make('button', 'member-card-more', item.videos?.length && item.images?.length ? 'Voir la capture et le témoignage complet' : 'Voir le témoignage complet');
    more.type = 'button'; more.addEventListener('click', () => openImages(item, more)); card.append(more);
    return card;
  }
  function audioCard(item) {
    const card = make('article', 'member-feed-item member-audio-card'); card.dataset.memberId = item.id;
    card.setAttribute('role', 'listitem');
    card.append(details(item, true));
    if (item.images?.length) {
      const see = make('button', 'member-audio-preview'); see.type = 'button';
      see.setAttribute('aria-label', `Voir ${item.images.length > 1 ? 'les captures' : 'la capture'} de ${item.name}`);
      const thumb = make('img'); thumb.src = item.images[0].src;
      thumb.alt = `Capture associée au vocal de ${item.name}`; thumb.loading = 'lazy'; thumb.decoding = 'async';
      const caption = make('span'); caption.append(make('strong', '', 'Son résultat, sa voix'), make('small', '', `${item.images.length > 1 ? item.images.length + ' captures' : 'Capture originale'} · Agrandir`));
      see.append(thumb, caption, make('span', 'member-audio-preview-arrow', '↗'));
      see.addEventListener('click', () => openImages(item, see)); card.append(see);
    }
    card.append(playlist(item.audios, item.name));
    return card;
  }
  function rail(records, id, label, createCard, cinema) {
    const shell = make('div', `member-feed-shell${cinema ? ' member-feed-cinema' : ''}`);
    const toolbar = make('div', 'member-feed-toolbar');
    const title = make('span', 'member-feed-hint', cinema ? 'GLISSE POUR EXPLORER' : 'ÉCOUTE LEUR EXPÉRIENCE');
    const controls = make('div', 'member-feed-controls');
    const count = make('span', 'member-feed-counter');
    const motion = make('button', 'member-feed-motion');
    const previous = make('button', 'member-feed-prev'); const next = make('button', 'member-feed-next');
    motion.type = previous.type = next.type = 'button';
    previous.setAttribute('aria-label', `${label} précédents`); next.setAttribute('aria-label', `${label} suivants`);
    previous.setAttribute('aria-controls', id); next.setAttribute('aria-controls', id);
    controls.append(count, motion, previous, next); toolbar.append(title, controls);
    const track = make('div', `member-feed-track ${cinema ? 'member-feed-visuals' : 'member-feed-audios'}`);
    track.id = id; track.tabIndex = 0; track.setAttribute('role', 'list'); track.setAttribute('aria-label', label);
    // Only mount the first cards. Subsequent records are appended as the visitor reaches
    // the end of the rail, so the full evidence catalogue remains available without
    // placing more than a thousand media controls in the initial page render.
    const batchSize = cinema ? 12 : 10;
    let rendered = 0;
    let observeCard = () => {};
    function appendBatch(target = rendered + batchSize) {
      const stop = Math.min(records.length, target);
      while (rendered < stop) {
        const card = createCard(records[rendered]);
        card.dataset.feedIndex = String(rendered);
        track.append(card);
        observeCard(card);
        rendered += 1;
      }
    }
    function ensureIndex(index) {
      if (index >= rendered) appendBatch(index + 1);
      return track.querySelector(`[data-feed-index="${index}"]`);
    }
    appendBatch();
    shell.append(toolbar, track);
    let paused = reduced.matches, hovering = false, inView = false, focusInside = false;
    let userUntil = performance.now() + 10000, previousTime = 0, fraction = 0;
    function motionLabel() {
      motion.classList.toggle('is-paused', paused);
      motion.setAttribute('aria-pressed', String(paused));
      motion.setAttribute('aria-label', `${paused ? 'Reprendre' : 'Mettre en pause'} le défilement des ${label.toLowerCase()}`);
      motion.hidden = reduced.matches;
    }
    motionLabel();
    motion.addEventListener('click', () => { paused = !paused; motionLabel(); });
    reduced.addEventListener('change', () => { paused = reduced.matches; motionLabel(); });
    const interrupt = () => { userUntil = performance.now() + 12000; };
    shell.addEventListener('pointerenter', () => { hovering = true; });
    shell.addEventListener('pointerleave', () => { hovering = false; });
    shell.addEventListener('focusin', () => { focusInside = true; });
    shell.addEventListener('focusout', event => { focusInside = shell.contains(event.relatedTarget); });
    track.addEventListener('pointerdown', interrupt, {passive:true});
    track.addEventListener('wheel', interrupt, {passive:true});
    new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; }, {threshold:0.12}).observe(shell);
    const nearCards = new Set();
    const cardObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => entry.isIntersecting ? nearCards.add(entry.target) : nearCards.delete(entry.target));
      if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
    }, {root:track,rootMargin:'0px 400px'});
    observeCard = card => cardObserver.observe(card);
    [...track.children].forEach(observeCard);
    const leftOf = child => child.offsetLeft;
    function nearest() {
      const candidates = nearCards.size ? [...nearCards] : [track.firstElementChild];
      return candidates.reduce((best, child) => Math.abs(leftOf(child) - track.scrollLeft) < Math.abs(leftOf(best) - track.scrollLeft) ? child : best, candidates[0]);
    }
    function navigateTo(target, immediate = false) {
      if (!target) return;
      // Keep enough cards after the target to align it, even after a full loop.
      while (leftOf(target) > track.scrollWidth - track.clientWidth && track.firstElementChild !== target) {
        track.append(track.firstElementChild);
      }
      const distance = Math.abs(leftOf(target) - track.scrollLeft);
      track.scrollTo({left:leftOf(target), behavior:immediate || reduced.matches || distance > track.clientWidth * 2 ? 'instant' : 'smooth'});
    }
    function go(direction) {
      interrupt(); const current = nearest(); const index = Number(current.dataset.feedIndex);
      const targetIndex = (index + direction + records.length) % records.length;
      const target = ensureIndex(targetIndex);
      navigateTo(target);
    }
    previous.addEventListener('click', () => go(-1)); next.addEventListener('click', () => go(1));
    track.addEventListener('keydown', event => {
      if (event.target !== track) return;
      if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) event.preventDefault();
      if (event.key === 'ArrowLeft') go(-1);
      if (event.key === 'ArrowRight') go(1);
      if (event.key === 'Home' || event.key === 'End') {
        interrupt(); const target = ensureIndex(event.key === 'Home' ? 0 : records.length - 1);
        navigateTo(target, true);
      }
    });
    let scheduled = false;
    function update() {
      scheduled = false;
      const current = nearest(); if (!current) return;
      const currentIndex = Number(current.dataset.feedIndex);
      if (currentIndex >= rendered - 4 && rendered < records.length) appendBatch();
      const nextText = `${currentIndex + 1} / ${records.length}`;
      if (count.textContent !== nextText) count.textContent = nextText;
      const viewportCenter = track.scrollLeft + track.clientWidth / 2;
      // Only nearby cards need perspective updates, even with hundreds of records.
      nearCards.forEach(child => {
        const distance = leftOf(child) + child.offsetWidth / 2 - viewportCenter;
        if (Math.abs(distance) > track.clientWidth + child.offsetWidth) return;
        const normalized = Math.max(-1, Math.min(1, distance / (track.clientWidth * .7)));
        child.style.setProperty('--feed-turn', `${-normalized * 10}deg`);
        child.style.setProperty('--feed-depth', `${Math.abs(normalized) * 12}px`);
      });
    }
    track.addEventListener('scroll', () => { if (!scheduled) { scheduled = true; requestAnimationFrame(update); } }, {passive:true});
    new ResizeObserver(update).observe(track); requestAnimationFrame(update);
    function animate(now) {
      const elapsed = previousTime ? Math.min(45, now - previousTime) : 0; previousTime = now;
      const playing = activePlayer && !activePlayer.paused && !activePlayer.ended;
      const run = inView && !paused && !hovering && !focusInside && !playing && !dialog.open && !document.hidden && now > userUntil && !reduced.matches;
      track.classList.toggle('member-feed-moving', run);
      if (run && records.length > 1) {
        fraction += elapsed * (cinema ? .018 : .014);
        if (fraction >= 1) {
          const first = track.firstElementChild;
          if (track.scrollLeft > first.offsetWidth + 60) {
            // Recycle only a fully off-screen card; the opening sequence stays intact.
            const anchor = first.nextElementSibling;
            const before = leftOf(anchor); const position = track.scrollLeft;
            track.append(first);
            track.scrollLeft = position + leftOf(anchor) - before;
          }
          track.scrollLeft += Math.floor(fraction); fraction %= 1;
        }
      }
      requestAnimationFrame(animate);
    }
    requestAnimationFrame(animate); return shell;
  }
  const visuals = (catalog.visualOrder || items.map(i => i.id)).map(id => byId.get(id)).filter(Boolean);
  const audios = items.filter(item => item.audios?.length);
  section.dataset.horizontalFeed = 'true';
  if (visualMount) visualMount.replaceWith(rail(visuals, 'member-visual-feed', 'Témoignages visuels', visualCard, true));
  if (audioMount) audioMount.replaceWith(rail(audios, 'member-audio-feed', 'Témoignages audio', audioCard, false));
  const intro = section.querySelector('.proof-intro p');
  if (intro) intro.textContent = 'Des captures, des voix et des visages. Découvre les expériences partagées par les membres.';
  const counts = make('p', 'member-feed-summary');
  counts.textContent = audioOnly
    ? `${audios.length.toLocaleString('fr-FR')} témoignages audio disponibles`
    : `${catalog.totals.testimonials.toLocaleString('fr-FR')} témoignages · ${catalog.totals.images} captures · ${catalog.totals.audios} audios · ${catalog.totals.videos} vidéos`;
  section.querySelector('.proof-intro')?.append(counts);
})();
