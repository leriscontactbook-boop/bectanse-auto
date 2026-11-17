(async () => {
  'use strict';
  const section = document.getElementById('temoignages');
  if (!section || section.dataset.horizontalFeed) return;
  const visualMount = section.querySelector('[data-member-feed="visuals"]');
  const audioMount = section.querySelector('[data-member-feed="audios"]');
  if (!visualMount && !audioMount) return;
  const audioOnly = !visualMount && Boolean(audioMount);
  // The public landing ships its own verified catalogue. Keeping every media
  // request under the same release prevents an external GitHub fetch from
  // delaying or hiding the testimonial rail in production.
  // All of the verified captures and voice notes live on the persistent media
  // store.  Do not duplicate the catalogue in the front-end release: using
  // this stable endpoint keeps the full wall available after each deploy.
  const localTestimonials = '/testimonials';
  const testimonialBase = localTestimonials;
  let catalog = window.__BECTANSE_TESTIMONIALS__ || null;
  if (!catalog) {
    try {
      const response = await fetch(`${testimonialBase}/temoignages.json?v=97b3374a9dc4`);
      if (!response.ok) return;
      catalog = await response.json();
    } catch (_) { return; }
  }
  if (!catalog.items?.length) return;
  // The catalog keeps source-relative media paths. Point them at this release.
  catalog = JSON.parse(JSON.stringify(catalog).replaceAll('/static/temoignages', testimonialBase));
  // Source messages can contain reaction emoji. They are not part of the visual
  // system of the public page, so retain the testimony wording while omitting them.
  const withoutEmoji = value => String(value ?? '').replace(/\p{Extended_Pictographic}|\uFE0F|\u20E3/gu, '').replace(/[ \t]+\n/g, '\n').replace(/ {2,}/g, ' ').trim();
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = withoutEmoji(text);
    return el;
  };
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const items = catalog.items;
  // The public wall uses first names only. This also prevents a compound source
  // label such as "Angel/C" from being rendered as an identifier.
  const displayName = value => String(value || 'Membre').trim().split(/\s+/)[0].split(/[\/|]/)[0] || 'Membre';
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
    line.append(make('h4', '', displayName(item.name)));
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
      image.src = file.src; image.alt = file.alt || `Résultat partagé par ${displayName(item.name)}`;
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
    if (item.audios?.length) modalBody.append(playlist(item.audios, displayName(item.name)));
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
      video.setAttribute('aria-label', `Témoignage vidéo de ${displayName(item.name)}`);
      video.addEventListener('play', () => pauseOthers(video));
      card.append(video);
    } else if (item.images?.length) {
      card.classList.add('member-feed-photo');
      const open = make('button', 'member-feed-photo-open'); open.type = 'button';
      open.setAttribute('aria-label', `Agrandir ${item.images.length > 1 ? 'les captures' : 'la capture'} de ${displayName(item.name)}`);
      const image = make('img'); image.src = item.images[0].src;
      image.alt = item.images[0].alt || `Résultat partagé par ${displayName(item.name)}`;
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
      card.append(playlist(item.audios, displayName(item.name)));
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
      see.setAttribute('aria-label', `Voir ${item.images.length > 1 ? 'les captures' : 'la capture'} de ${displayName(item.name)}`);
      const thumb = make('img'); thumb.src = item.images[0].src;
      thumb.alt = `Capture associée au vocal de ${displayName(item.name)}`; thumb.loading = 'lazy'; thumb.decoding = 'async';
      const caption = make('span'); caption.append(make('strong', '', 'Son résultat, sa voix'), make('small', '', `${item.images.length > 1 ? item.images.length + ' captures' : 'Capture originale'} · Agrandir`));
      see.append(thumb, caption, make('span', 'member-audio-preview-arrow', '↗'));
      see.addEventListener('click', () => openImages(item, see)); card.append(see);
    }
    card.append(playlist(item.audios, displayName(item.name)));
    return card;
  }
  function rail(records, id, label, createCard, cinema, totalCount = records.length) {
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
    // Keep a bounded sliding window: the catalogue can be large, but the DOM and media
    // controls must stay small on mobile Safari. New records are mounted as the visitor
    // advances; old off-screen cards are released from memory.
    const batchSize = 5;
    const maxMounted = 15;
    let windowStart = 0;
    let rendered = 0;
    let observeCard = () => {};
    function appendBatch(target = windowStart + rendered + batchSize) {
      const stop = Math.min(records.length, target, windowStart + maxMounted);
      while (windowStart + rendered < stop) {
        const absoluteIndex = windowStart + rendered;
        const card = createCard(records[absoluteIndex]);
        card.dataset.feedIndex = String(absoluteIndex);
        track.append(card);
        observeCard(card);
        rendered += 1;
      }
    }
    function ensureIndex(index) {
      if (!track.querySelector(`[data-feed-index="${index}"]`)) {
        windowStart = Math.max(0, Math.min(index - 3, records.length - maxMounted));
        [...track.children].forEach(card => cardObserver.unobserve(card));
        track.replaceChildren();
        rendered = 0;
        nearCards.clear();
        track.scrollLeft = 0;
        appendBatch(windowStart + maxMounted);
      }
      if (index >= windowStart + rendered) appendBatch(index + 1);
      return track.querySelector(`[data-feed-index="${index}"]`);
    }
    shell.append(toolbar, track);
    let paused = reduced.matches, hovering = false, inView = false, focusInside = false;
    let userUntil = performance.now() + 10000, previousTime = 0, fraction = 0;
    let animationFrame = 0, restartTimer = 0;
    function motionLabel() {
      motion.classList.toggle('is-paused', paused);
      motion.setAttribute('aria-pressed', String(paused));
      motion.setAttribute('aria-label', `${paused ? 'Reprendre' : 'Mettre en pause'} le défilement des ${label.toLowerCase()}`);
      motion.hidden = reduced.matches;
    }
    motionLabel();
    const shouldRun = () => inView && !paused && !hovering && !focusInside &&
      !(activePlayer && !activePlayer.paused && !activePlayer.ended) && !dialog.open &&
      !document.hidden && performance.now() > userUntil && !reduced.matches;
    function stopMotion() {
      if (animationFrame) cancelAnimationFrame(animationFrame);
      animationFrame = 0;
      track.classList.remove('member-feed-moving');
    }
    function startMotion() {
      if (shouldRun() && !animationFrame) animationFrame = requestAnimationFrame(animate);
    }
    function refreshMotion() {
      if (shouldRun()) startMotion(); else stopMotion();
      clearTimeout(restartTimer);
      if (inView && !paused && !reduced.matches && performance.now() < userUntil) {
        restartTimer = setTimeout(startMotion, Math.max(40, userUntil - performance.now() + 10));
      }
    }
    motion.addEventListener('click', () => { paused = !paused; motionLabel(); refreshMotion(); });
    reduced.addEventListener('change', () => { paused = reduced.matches; motionLabel(); refreshMotion(); });
    const interrupt = () => { userUntil = performance.now() + 12000; refreshMotion(); };
    shell.addEventListener('pointerenter', () => { hovering = true; refreshMotion(); });
    shell.addEventListener('pointerleave', () => { hovering = false; refreshMotion(); });
    shell.addEventListener('focusin', () => { focusInside = true; refreshMotion(); });
    shell.addEventListener('focusout', event => { focusInside = shell.contains(event.relatedTarget); refreshMotion(); });
    ['play','pause','ended'].forEach(type => shell.addEventListener(type, refreshMotion, true));
    document.addEventListener('visibilitychange', refreshMotion);
    track.addEventListener('pointerdown', interrupt, {passive:true});
    track.addEventListener('wheel', interrupt, {passive:true});
    const nearCards = new Set();
    new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; refreshMotion(); }, {threshold:0.12}).observe(shell);
    const cardObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => entry.isIntersecting ? nearCards.add(entry.target) : nearCards.delete(entry.target));
      if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
    }, {root:track,rootMargin:'0px 400px'});
    observeCard = card => cardObserver.observe(card);
    appendBatch(windowStart + maxMounted);
    [...track.children].forEach(observeCard);
    const leftOf = child => child.offsetLeft;
    function nearest() {
      const candidates = nearCards.size ? [...nearCards] : [track.firstElementChild];
      return candidates.reduce((best, child) => Math.abs(leftOf(child) - track.scrollLeft) < Math.abs(leftOf(best) - track.scrollLeft) ? child : best, candidates[0]);
    }
    function navigateTo(target, immediate = false) {
      if (!target) return;
      const distance = Math.abs(leftOf(target) - track.scrollLeft);
      // `instant` is not a standard ScrollTo behavior and is ignored by some Safari
      // versions. `auto` gives the intended immediate movement consistently.
      track.scrollTo({left:leftOf(target), behavior:immediate || reduced.matches || distance > track.clientWidth * 2 ? 'auto' : 'smooth'});
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
      if (currentIndex >= windowStart + rendered - 4 && windowStart + rendered < records.length) {
        // The current card is already near the rail's right edge. Release up to five
        // cards behind it, preserve the visible position, then mount the next five.
        if (track.children.length >= maxMounted && currentIndex >= windowStart + 9) {
          const release = Math.min(batchSize, track.children.length - (maxMounted - batchSize));
          let removedWidth = 0;
          for (let i = 0; i < release; i += 1) {
            const first = track.firstElementChild;
            if (!first || first === current) break;
            removedWidth += first.getBoundingClientRect().width;
            const gap = parseFloat(getComputedStyle(track).columnGap || getComputedStyle(track).gap) || 0;
            removedWidth += gap;
            cardObserver.unobserve(first); nearCards.delete(first); first.remove();
            windowStart += 1; rendered -= 1;
          }
          track.scrollLeft = Math.max(0, track.scrollLeft - removedWidth);
        }
        appendBatch();
      }
      // The visible rail is a curated, lazy-loaded selection. Its progress still
      // reflects the real archived catalogue so the social proof is not capped
      // by the number of cards mounted at one time.
      const realTotal = Math.max(records.length, Number(totalCount) || records.length);
      const nextText = `${currentIndex + 1} / ${realTotal.toLocaleString('fr-FR')}`;
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
      animationFrame = 0;
      if (!shouldRun()) { track.classList.remove('member-feed-moving'); return; }
      const elapsed = previousTime ? Math.min(45, now - previousTime) : 0; previousTime = now;
      track.classList.add('member-feed-moving');
      if (records.length > 1) {
        fraction += elapsed * (cinema ? .018 : .014);
        if (fraction >= 1) {
          track.scrollLeft += Math.floor(fraction); fraction %= 1;
        }
      }
      animationFrame = requestAnimationFrame(animate);
    }
    refreshMotion(); return shell;
  }
  const sourceVisuals = (catalog.visualOrder || items.map(i => i.id)).map(id => byId.get(id)).filter(Boolean);
  const amountOf = item => {
    if (Number.isFinite(item.result?.amount)) return item.result.amount;
    const raw = String(item.result?.value || '').replace(/[^\d,.-]/g, '');
    if (!raw) return -1;
    const comma = raw.lastIndexOf(','), dot = raw.lastIndexOf('.');
    const decimal = comma > dot ? ',' : dot > comma ? '.' : '';
    const normalized = decimal ? raw.replace(/[,.]/g, (mark, index) => mark === decimal && index === Math.max(comma, dot) ? '.' : '') : raw;
    const value = Number(normalized);
    return Number.isFinite(value) ? value : -1;
  };
  // The main carousel is intentionally editorial, not an unfiltered archive:
  // a proof must pair a real capture with a member audio or video. We retain
  // results of at least 400 € and then put the strongest evidence first.
  // Small isolated gains never interrupt the social-proof storyline.
  const pairedProofs = sourceVisuals.filter(item => item.images?.length && (item.audios?.length || item.videos?.length));
  // Editorial rhythm at the start of the rail: strong capture with a voice,
  // video, strong capture with a voice, video, then one final video. It makes
  // the social proof feel alive before the result-led sequence continues.
  const featuredIds = ['bectanse-870', 'moneyclub-1136', 'bectanse-308', 'bectanse-965', 'bectanse-820', 'legacy-wendy'];
  const featuredCandidates = featuredIds.map(id => byId.get(id)).filter(Boolean);
  const featured = featuredCandidates.filter(item => (item.images?.length && (item.audios?.length || item.videos?.length)) || item.videos?.length);
  const featuredSet = new Set(featured.map(item => item.id));
  const premiumProofs = pairedProofs
    .filter(item => !featuredSet.has(item.id) && amountOf(item) >= 400)
    .sort((a, b) => amountOf(b) - amountOf(a));
  const visuals = [...featured, ...premiumProofs];
  const audios = visuals.filter(item => item.audios?.length);
  section.dataset.horizontalFeed = 'true';
  const realTestimonialTotal = catalog.totals?.testimonials || visuals.length;
  if (visualMount) visualMount.replaceWith(rail(visuals, 'member-visual-feed', 'Témoignages visuels', visualCard, true, realTestimonialTotal));
  if (audioMount) audioMount.replaceWith(rail(audios, 'member-audio-feed', 'Témoignages audio', audioCard, false));
  const counts = make('p', 'member-feed-summary');
  counts.textContent = audioOnly
    ? `${audios.length.toLocaleString('fr-FR')} témoignages audio accompagnés d’une capture`
    : `Sélection de ${visuals.length} retours complets avec capture, audio ou vidéo`;
  section.querySelector('.proof-intro')?.append(counts);
})();
