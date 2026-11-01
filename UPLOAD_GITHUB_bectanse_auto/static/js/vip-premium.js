(() => {
  'use strict';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const root = document.documentElement;
  // One reversible scroll director owns scene progress. Each scene has separate
  // editorial and media layers, so transitions never overwrite layout transforms.
  const sceneConfigs = [
    ['histoire', [], 'portal', 'Entrée'],
    ['univers', ['.section-head', '.vip-world-tabs'], 'split', 'Univers'],
    ['ecosysteme', ['.section-head', '.journey-pillars'], 'pillars', 'Parcours'],
    ['application', ['.hero-grid > div:first-child', '.product-stage'], 'device', 'Application'],
    ['bectanse-auto', ['.auto-heading', '[data-vip-copy-video]', '.auto-benefits', '.auto-guide', '.auto-conversion'], 'relay', 'Auto'],
    ['journal-apercu', ['.journal-copy', '.journal-product'], 'replay', 'Journal'],
    ['formation', ['.academy-value-head', '.training-book', '.included-panel', '.curriculum'], 'unfold', 'Formation'],
    ['temoignages', ['.proof-intro', '.member-feed-shell'], 'stream', 'Membres'],
    ['offres', ['.sales-intro', '.plan-grid', '.benefit-heading', '.benefits', '.sales-faq'], 'offers', 'Offres'],
    ['essai-candidature', ['.trial-cinema', '.trial-grid > div:first-child', '.trial-stage', '.trial-bottom'], 'ticket', 'Essai'],
    ['capture', ['.explorer-copy', '.explorer-panel'], 'build', 'Explorer'],
    ['analyses-ia', ['.ai-copy', '.ai-quality', '.ad-demo', '.ai-guide', '.ai-buy'], 'scan', 'Outils'],
  ];
  const scenes = sceneConfigs.map(([id, selectors, motion, label]) => {
    const scene = document.getElementById(id);
    if (!scene) return null;
    const units = selectors.flatMap(selector => [...scene.querySelectorAll(selector)]);
    units.forEach((unit, index) => {
      unit.classList.add(index % 2 ? 'scene-media' : 'scene-content');
      if (index > 1) unit.classList.add('scene-stagger');
    });
    scene.classList.add('vip-scene');
    scene.dataset.motion = motion;
    scene.dataset.sceneLabel = label;
    if (id !== 'histoire' && !scene.querySelector('.vip-scene-light')) {
      const light = document.createElement('img');
      light.className = 'vip-scene-light';
      light.src = '/static/vip/assets/question-hover-light.webp';
      light.alt = '';
      light.setAttribute('aria-hidden', 'true');
      scene.prepend(light);
    }
    scene.dataset.sceneOrder = String(sceneConfigs.findIndex(([sceneId]) => sceneId === id));
    return scene;
  }).filter(Boolean);
  const finale = document.querySelector('.final');
  if (finale) {
    finale.querySelectorAll(':scope > *').forEach((unit, index) => {
      unit.classList.add(index ? 'scene-media' : 'scene-content');
    });
    scenes.push(finale);
  }
  if (!scenes.length) return;
  root.classList.add('vip-motion');

  // Compact waypoint navigation: it mirrors the active scene without replacing
  // normal page scrolling or the existing commercial/header links.
  const navigableScenes = scenes.filter((scene) => scene.id && scene.dataset.sceneLabel);

  const routeNav = document.createElement('nav');
  routeNav.className = 'vip-route-nav';
  routeNav.setAttribute('aria-label', 'Progression dans la page');
  routeNav.innerHTML = '<span class="vip-route-nav-status" aria-live="polite"></span><div class="vip-route-nav-points"></div>';
  const routeStatus = routeNav.querySelector('.vip-route-nav-status');
  const routePoints = routeNav.querySelector('.vip-route-nav-points');
  const routeButtons = navigableScenes.map((scene, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'vip-route-nav-point';
    button.setAttribute('aria-label', `Aller à la section ${scene.dataset.sceneLabel}`);
    button.setAttribute('aria-current', 'false');
    button.dataset.sceneTarget = scene.id;
    button.addEventListener('click', () => {
      const target = document.getElementById(button.dataset.sceneTarget);
      if (!target) return;
      target.scrollIntoView({behavior: reduced.matches ? 'auto' : 'smooth', block: 'start'});
    });
    routePoints.append(button);
    return button;
  });
  document.body.append(routeNav);
  let activeSceneIndex = -1;
  const setActiveScene = (index) => {
    if (index === activeSceneIndex || index < 0) return;
    activeSceneIndex = index;
    routeButtons.forEach((button, pointIndex) => {
      const isCurrent = pointIndex === index;
      button.classList.toggle('is-active', isCurrent);
      button.setAttribute('aria-current', String(isCurrent));
    });
    const scene = navigableScenes[index];
    if (routeStatus && scene) routeStatus.textContent = `${String(index + 1).padStart(2, '0')} · ${scene.dataset.sceneLabel}`;
  };

  // The entry remains functional without JavaScript; this code only scrubs its visual transition.
  const entry = document.getElementById('vip-entry');
  const entryLight = entry?.querySelector('.vip-entry-light');
  const entryPortal = entry?.querySelector('.vip-entry-portal');
  const entryCopy = entry?.querySelector('.vip-entry-copy');
  const entryGrid = entry?.querySelector('.vip-entry-grid');
  const entryStage = entry?.querySelector('.vip-entry-stage');
  const entryStory = document.getElementById('story-start');
  const entryButton = entry?.querySelector('.vip-entry-button');
  const entryPnl = entry?.querySelector('[data-entry-pnl]');
  const entryRoi = entry?.querySelector('[data-entry-roi]');
  const entryCandles = entry ? [...entry.querySelectorAll('[data-entry-candle]')] : [];
  const applicationScene = document.getElementById('application');
  const academyPreview = applicationScene?.querySelector('academy-preview');
  const phoneScreenOrder = ['accueil', 'academie', 'trader-lab', 'live', 'faq', 'journal'];
  let lastPhoneScreen = '';
  const entryCandleCount = entry?.querySelector('[data-entry-candle-count]');
  const overlight = (() => {
    const node = document.createElement('img');
    node.className = 'vip-overlight';
    node.src = '/static/vip/assets/question-hover-light.webp';
    node.alt = '';
    node.setAttribute('aria-hidden', 'true');
    document.body.append(node);
    return node;
  })();
  if (entryButton) entryButton.addEventListener('click', (event) => {
    // The href remains a complete no-JS fallback; with JS we travel through the same scrubbed scene.
    event.preventDefault();
    entry.dataset.entered = 'true';
    // The destination is measured in the live document so the same track works after responsive reflow.
    // Finish the pinned track itself: the story is revealed by its final scrub frames.
    const travelEnd = entry.offsetTop + Math.max(0, entry.offsetHeight - window.innerHeight);
    window.scrollTo({top: travelEnd, behavior: reduced.matches ? 'auto' : 'smooth'});
  });

  const live = document.getElementById('bectanse-live');
  const candles = live ? [...live.querySelectorAll('.bectanse-live-candles > g')] : [];
  const period = live?.querySelector('.bectanse-live-period strong');
  const candleMetric = live?.querySelector('[data-live-candles]');
  const prices = live ? [...live.querySelectorAll('.bectanse-live-price-scale span')] : [];
  const ranges = ['09:00 — 10:00', '09:30 — 10:30', '10:00 — 11:00'];
  const scaleStates = [['—','—','—','—','—'],['—','—','—','—','—'],['—','—','—','—','—']];

  let frame = 0;
  const clamp = (value, minimum = 0, maximum = 1) => Math.max(minimum, Math.min(maximum, value));
  const progressFor = (element, viewport) => {
    const rect = element.getBoundingClientRect();
    return clamp((viewport - rect.top) / Math.max(1, rect.height + viewport));
  };
  const render = () => {
    frame = 0;
    const viewport = window.innerHeight || document.documentElement.clientHeight;
    const documentHeight = Math.max(1, document.documentElement.scrollHeight - viewport);
    const pageProgress = reduced.matches ? 1 : clamp(window.scrollY / documentHeight);
    root.style.setProperty('--vip-page-progress', pageProgress.toFixed(3));
    if (entry) {
      const rect = entry.getBoundingClientRect();
      const progress = reduced.matches ? 1 : clamp(-rect.top / Math.max(1, entry.offsetHeight - viewport));
      // Data completes first. The final frames are reserved for the portal impact and the reveal.
      const dataProgress = clamp(progress / .88);
      const impact = clamp((progress - .89) / .055) * (1 - clamp((progress - .975) / .025));
      entry.style.setProperty('--entry-progress', progress.toFixed(3));
      entry.style.setProperty('--entry-impact', impact.toFixed(3));
      entry.style.setProperty('--entry-travel', clamp((progress - .12) / .88).toFixed(3));
      entry.style.setProperty('--entry-arrival', clamp((progress - .72) / .28).toFixed(3));
      if (entryCopy) {
        entryCopy.style.opacity = String(1 - progress * .97);
        entryCopy.style.transform = `translateY(${-progress * 82}px) scale(${1 + progress * .09})`;
      }
      if (entryPortal) {
        const travel = clamp((progress - .10) / .90);
        entryPortal.style.opacity = String(.74 + travel * .22);
        entryPortal.style.transform = `perspective(1200px) translateY(${travel * 42}px) scale(${1 + travel * 2.25})`;
      }
      if (entryGrid) entryGrid.style.transform = `perspective(850px) translateY(${progress * 112}px) scale(${1 + progress * .62})`;
      if (entryStage) entryStage.style.setProperty('--entry-flash', String(impact));
      if (entryStory) {
        const arrival = clamp((progress - .88) / .12);
        entryStory.style.opacity = String(arrival);
        entryStory.style.transform = `translateY(${(1 - arrival) * 64}px) scale(${.97 + arrival * .03})`;
      }
      if (entryLight) {
        entryLight.style.opacity = String(.055 + progress * .16 + impact * .08);
        entryLight.style.transform = `translate(${(-18 + progress * 48).toFixed(1)}%, ${(-7 + progress * 12).toFixed(1)}%) scale(${(.84 + progress * .44).toFixed(3)})`;
      }
      const pnl = Math.round(dataProgress * 1000 / 10) * 10;
      if (entryPnl) entryPnl.innerHTML = `${pnl > 0 ? '+' : ''}${new Intl.NumberFormat('fr-FR').format(pnl)}&nbsp;€`;
      if (entryRoi) entryRoi.innerHTML = `+${(dataProgress * .20).toLocaleString('fr-FR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}&nbsp;%`;
      let revealedCandles = 0;
      entryCandles.forEach((candle, index) => {
        const reveal = clamp(dataProgress * entryCandles.length - index + 1);
        if (reveal >= .98) revealedCandles += 1;
        candle.style.opacity = String(.14 + reveal * .86);
        candle.style.transform = `translateY(${((1 - reveal) * 42).toFixed(1)}px) scaleY(${(.42 + reveal * .58).toFixed(3)})`;
      });
      if (entryCandleCount) entryCandleCount.textContent = `${revealedCandles} / ${entryCandles.length} bougies`;
    }
    scenes.forEach((scene, index) => {
      const progress = reduced.matches ? 1 : progressFor(scene, viewport);
      // A shared overlap: outgoing scene retires while the next arrives.  The
      // midpoint remains a valid composed state in both scroll directions.
      const enter = clamp(progress * 1.46);
      const exit = clamp((progress - .54) * 2.18);
      const bridge = clamp((progress - .34) / .48);
      scene.classList.toggle('is-scene-active', progress > .16 && progress < .94);
      scene.style.setProperty('--scene-progress', progress.toFixed(3));
      scene.style.setProperty('--scene-bridge', bridge.toFixed(3));
      scene.style.setProperty('--scene-enter', enter.toFixed(3));
      scene.style.setProperty('--scene-exit', exit.toFixed(3));
      scene.style.setProperty('--scene-depth', String(index % 2 ? 1 : -1));
      scene.style.setProperty('--scene-content-opacity', (.58 + enter * .42 - exit * .06).toFixed(3));
      scene.style.setProperty('--scene-content-y', `${((1 - enter) * 56 - exit * 38).toFixed(1)}px`);
      scene.style.setProperty('--scene-media-opacity', (.64 + enter * .36 - exit * .05).toFixed(3));
      scene.style.setProperty('--scene-media-y', `${((1 - enter) * 74 - exit * 24).toFixed(1)}px`);
      scene.style.setProperty('--scene-media-x', `${((index % 2 ? 1 : -1) * (1 - enter) * 28).toFixed(1)}px`);
      scene.style.setProperty('--scene-media-scale', (.945 + enter * .055 - exit * .012).toFixed(3));
      scene.style.setProperty('--scene-stagger-y', `${((1 - enter) * 82 - exit * 20).toFixed(1)}px`);
      const sceneLight = scene.querySelector('.vip-scene-light');
      if (sceneLight) {
        sceneLight.style.opacity = (.025 + enter * .14 - exit * .05).toFixed(3);
        sceneLight.style.transform = `translate(${(-22 + pageProgress * 48 + (index % 2 ? 10 : -10)).toFixed(1)}%, ${(-8 + enter * 14).toFixed(1)}%) scale(${(.88 + enter * .24).toFixed(3)})`;
      }
    });
    const focusedScene = navigableScenes.reduce((best, scene, index) => {
      const rect = scene.getBoundingClientRect();
      const distance = Math.abs(rect.top + rect.height * .5 - viewport * .5);
      return !best || distance < best.distance ? {index, distance} : best;
    }, null);
    if (focusedScene) setActiveScene(focusedScene.index);

    // The actual Academy views advance through the phone while its host stage is pinned.
    // Direct tab clicks remain available; scroll only updates when the segment changes.
    if (academyPreview?.show && applicationScene) {
      const appRect = applicationScene.getBoundingClientRect();
      const travel = Math.max(1, applicationScene.offsetHeight - viewport);
      const phoneProgress = reduced.matches ? 1 : clamp(-appRect.top / travel);
      const screenIndex = Math.min(phoneScreenOrder.length - 1, Math.floor(phoneProgress * phoneScreenOrder.length));
      const screen = phoneScreenOrder[screenIndex];
      if (screen && screen !== lastPhoneScreen) {
        academyPreview.show(screen);
        lastPhoneScreen = screen;
      }
      applicationScene.style.setProperty('--phone-progress', phoneProgress.toFixed(3));
    }
    if (overlight) {
      const active = scenes.reduce((best, scene, index) => {
        const distance = Math.abs(scene.getBoundingClientRect().top + scene.offsetHeight * .5 - viewport * .5);
        return !best || distance < best.distance ? { index, distance } : best;
      }, null);
      const scenePhase = active ? active.index / Math.max(1, scenes.length - 1) : pageProgress;
      const x = 14 + ((scenePhase * 74) + (pageProgress * 18)) % 76;
      const y = 22 + (scenePhase % 2) * 34 + Math.sin(pageProgress * Math.PI * 3) * 7;
      overlight.style.setProperty('--overlight-opacity', String(reduced.matches ? 0 : .115 + Math.min(.095, pageProgress * .06)));
      overlight.style.transform = `translate(-50%, -50%) translate(${x.toFixed(1)}vw, ${y.toFixed(1)}vh) scale(${(.82 + pageProgress * .30).toFixed(3)})`;
    }
    if (!live) return;
    const progress = reduced.matches ? 1 : progressFor(live, viewport);
    live.style.setProperty('--live-progress', progress.toFixed(3));
    let visible = 0;
    candles.forEach((candle, index) => {
      const reveal = clamp(progress * (candles.length + 7) - index + 1);
      if (reveal > .5) visible += 1;
      candle.style.opacity = String(reveal);
      candle.style.transform = `translateY(${((1 - reveal) * 30).toFixed(1)}px) scaleY(${(.68 + reveal * .32).toFixed(3)})`;
    });
    const rangeIndex = Math.min(ranges.length - 1, Math.floor(progress * ranges.length));
    if (period) period.textContent = ranges[rangeIndex];
    if (candleMetric) candleMetric.textContent = `${visible} / ${candles.length}`;
    prices.forEach((node, index) => { node.textContent = scaleStates[rangeIndex][index]; });
  };
  const requestRender = () => {
    if (!frame) frame = requestAnimationFrame(render);
  };
  addEventListener('scroll', requestRender, {passive:true});
  addEventListener('resize', requestRender, {passive:true});
  reduced.addEventListener?.('change', requestRender);

  // Accessible editorial accordion: one useful detail at a time, not six large cards.
  document.querySelectorAll('[data-benefit-accordion]').forEach((accordion) => {
    const items = [...accordion.querySelectorAll('[data-benefit-item]')];
    items.forEach((item, index) => {
      const button = item.querySelector('.benefit-toggle');
      const detail = item.querySelector('.benefit-detail');
      if (!button || !detail) return;
      const detailId = `benefit-detail-${index + 1}`;
      detail.id = detailId;
      button.setAttribute('aria-controls', detailId);
      button.addEventListener('click', () => {
        const willOpen = button.getAttribute('aria-expanded') !== 'true';
        items.forEach((other) => {
          const otherButton = other.querySelector('.benefit-toggle');
          const otherDetail = other.querySelector('.benefit-detail');
          if (otherButton) otherButton.setAttribute('aria-expanded', 'false');
          if (otherDetail) otherDetail.hidden = true;
        });
        button.setAttribute('aria-expanded', String(willOpen));
        detail.hidden = !willOpen;
      });
    });
  });

  // The offers remain real checkout cards. On small screens they become a controlled,
  // native horizontal track with previous/next controls and a live position indicator.
  document.querySelectorAll('.academy-sales .plan-grid').forEach((track) => {
    const plans = [...track.querySelectorAll('.plan')];
    if (plans.length < 2) return;
    track.classList.add('offers-swipe-track');
    const controls = document.createElement('div');
    controls.className = 'offers-swipe-controls';
    controls.innerHTML = '<button type="button" class="offers-swipe-prev" aria-label="Voir la formule précédente">←</button><span aria-live="polite" aria-atomic="true"></span><button type="button" class="offers-swipe-next" aria-label="Voir la formule suivante">→</button>';
    track.after(controls);
    const label = controls.querySelector('span');
    const sync = () => {
      const current = plans.reduce((best, plan, index) => Math.abs(plan.offsetLeft - track.scrollLeft) < Math.abs(plans[best].offsetLeft - track.scrollLeft) ? index : best, 0);
      label.textContent = `${current + 1} / ${plans.length}`;
      controls.querySelector('.offers-swipe-prev').disabled = current === 0;
      controls.querySelector('.offers-swipe-next').disabled = current === plans.length - 1;
    };
    const move = (direction) => {
      const current = plans.reduce((best, plan, index) => Math.abs(plan.offsetLeft - track.scrollLeft) < Math.abs(plans[best].offsetLeft - track.scrollLeft) ? index : best, 0);
      plans[Math.max(0, Math.min(plans.length - 1, current + direction))].scrollIntoView({behavior: reduced.matches ? 'auto' : 'smooth', block: 'nearest', inline: 'start'});
    };
    controls.querySelector('.offers-swipe-prev').addEventListener('click', () => move(-1));
    controls.querySelector('.offers-swipe-next').addEventListener('click', () => move(1));
    track.addEventListener('scroll', () => requestAnimationFrame(sync), {passive:true});
    sync();
  });
  // Recalculate the same scene track after responsive changes, media loads and
  // accordions/carousels change height.  This mirrors CryptOwl's refresh coordinator
  // without introducing a competing scroll library.
  let refreshFrame = 0;
  const scheduleRefresh = () => {
    cancelAnimationFrame(refreshFrame);
    refreshFrame = requestAnimationFrame(() => {
      refreshFrame = 0;
      requestRender();
    });
  };
  const observer = 'ResizeObserver' in window ? new ResizeObserver(scheduleRefresh) : null;
  scenes.forEach(scene => observer?.observe(scene));
  document.querySelectorAll('img,video,audio').forEach(media => {
    media.addEventListener('load', scheduleRefresh, { passive: true });
    media.addEventListener('loadedmetadata', scheduleRefresh, { passive: true });
  });
  addEventListener('orientationchange', scheduleRefresh, { passive: true });
  document.fonts?.ready?.then(scheduleRefresh);
  requestRender();
})();
