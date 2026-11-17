(() => {
  'use strict';
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(pointer: fine)');
  const progress = document.querySelector('.reading-progress');
  const hero = document.querySelector('.hero');
  const parallax = [...document.querySelectorAll('[data-parallax]')];
  let scrollFrame = 0;

  function paintScroll() {
    scrollFrame = 0;
    const available = document.documentElement.scrollHeight - innerHeight;
    if (progress) progress.style.transform = `scaleX(${available > 0 ? Math.min(1, scrollY / available) : 0})`;
    if (hero && !motion.matches && finePointer.matches) {
      const offset = Math.min(scrollY, hero.offsetHeight);
      parallax.forEach(el => { el.style.translate = `0 ${offset * Number(el.dataset.parallax)}px`; });
    }
  }
  function scheduleScroll() {
    if (!scrollFrame) scrollFrame = requestAnimationFrame(paintScroll);
  }
  window.addEventListener('scroll', scheduleScroll, {passive: true});
  window.addEventListener('resize', scheduleScroll, {passive: true});
  paintScroll();

  // Reveal only below-the-fold content; the page is readable without JavaScript.
  if ('IntersectionObserver' in window && !motion.matches) {
    const reveal = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.remove('is-waiting');
          reveal.unobserve(entry.target);
        }
      });
    }, {threshold: 0.08, rootMargin: '0px 0px -25px 0px'});
    document.querySelectorAll('.reveal').forEach(el => {
      if (el.getBoundingClientRect().top >= innerHeight) {
        el.classList.add('is-waiting');
        reveal.observe(el);
      }
    });
    motion.addEventListener('change', () => {
      if (motion.matches) {
        reveal.disconnect();
        document.querySelectorAll('.is-waiting').forEach(el => el.classList.remove('is-waiting'));
        parallax.forEach(el => { el.style.translate = ''; });
      }
    });
  }

  // A restrained magnetic response that never moves the hit area away from a finger.
  document.querySelectorAll('.magnetic').forEach(button => {
    button.addEventListener('pointermove', event => {
      if (!finePointer.matches || motion.matches) return;
      const rect = button.getBoundingClientRect();
      const x = (event.clientX - rect.left - rect.width / 2) * 0.035;
      const y = (event.clientY - rect.top - rect.height / 2) * 0.08;
      button.style.translate = `${x}px ${y}px`;
    });
    button.addEventListener('pointerleave', () => { button.style.translate = ''; });
    button.addEventListener('blur', () => { button.style.translate = ''; });
  });

  // The mobile shortcut appears once the main call to action leaves the screen.
  const mobile = document.querySelector('.mobile-cta');
  const mainCta = document.querySelector('.hero .button');
  if (mobile && mainCta && 'IntersectionObserver' in window) {
    new IntersectionObserver(entries => {
      const visible = entries[0].isIntersecting;
      mobile.classList.toggle('is-hidden', visible);
      mobile.inert = visible;
    }, {threshold: 0.3}).observe(mainCta);
  }

  document.querySelectorAll('video').forEach(video => {
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(entries => {
        if (!entries[0].isIntersecting) video.pause();
      }, {threshold: 0.15}).observe(video);
    }
  });

  const track = document.getElementById('life-gallery');
  const pause = document.getElementById('gallery-pause');
  if (!track || !pause) return;
  const originals = [...track.children];
  const copies = originals.map(card => {
    const copy = card.cloneNode(true);
    copy.setAttribute('aria-hidden', 'true');
    copy.querySelectorAll('a').forEach(link => link.tabIndex = -1);
    copy.querySelectorAll('img').forEach(img => { img.alt = ''; });
    track.appendChild(copy);
    return copy;
  });
  let cycle = 0;
  let position = 0;
  let auto = !motion.matches;
  let inView = false;
  let hovered = false;
  let focused = false;
  let touching = false;
  let resumeAt = 0;
  let lastTime = 0;
  let animation = 0;
  function measure() {
    cycle = copies[0].offsetLeft - originals[0].offsetLeft;
  }
  function syncControl() {
    pause.setAttribute('aria-pressed', String(!auto));
    pause.setAttribute('aria-label', auto ? 'Mettre le défilement en pause' : 'Activer le défilement automatique');
    pause.textContent = auto ? 'Mettre le défilement en pause' : 'Activer le défilement automatique';
  }
  function canAnimate() {
    return auto && inView && !document.hidden && !hovered && !focused && !touching;
  }
  function stop() {
    cancelAnimationFrame(animation);
    animation = 0;
    lastTime = 0;
  }
  function start() {
    if (canAnimate() && !animation) animation = requestAnimationFrame(tick);
  }
  function tick(time) {
    animation = 0;
    if (!canAnimate()) { lastTime = 0; return; }
    const delta = lastTime ? Math.min(time - lastTime, 50) : 0;
    lastTime = time;
    if (cycle > 0 && time >= resumeAt) {
      position = (position + delta * 0.045) % cycle;
      track.scrollLeft = position;
    }
    animation = requestAnimationFrame(tick);
  }
  pause.addEventListener('click', () => {
    auto = !auto;
    syncControl();
    auto ? start() : stop();
  });
  track.addEventListener('pointerenter', event => {
    if (event.pointerType === 'mouse' && finePointer.matches) { hovered = true; stop(); }
  });
  track.addEventListener('pointerleave', () => { hovered = false; start(); });
  track.addEventListener('pointerdown', event => {
    if (event.pointerType === 'mouse') { touching = true; stop(); }
  });
  function release() {
    if (!touching) return;
    touching = false;
    resumeAt = performance.now() + 4000;
    start();
  }
  window.addEventListener('pointerup', release, {passive: true});
  window.addEventListener('pointercancel', release, {passive: true});
  track.addEventListener('focusin', () => {
    if (finePointer.matches) { focused = true; stop(); }
  });
  track.addEventListener('focusout', event => {
    focused = track.contains(event.relatedTarget);
    start();
  });
  
  document.addEventListener('visibilitychange', () => {
    document.hidden ? stop() : start();
    if (document.hidden) document.querySelectorAll('video').forEach(video => video.pause());
  });
  motion.addEventListener('change', () => {
    auto = !motion.matches;
    syncControl();
    auto ? start() : stop();
  });
  if ('ResizeObserver' in window) new ResizeObserver(measure).observe(track);
  else window.addEventListener('resize', measure);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => {
      inView = entries[0].isIntersecting;
      inView ? start() : stop();
    }, {rootMargin: '100px'}).observe(track);
  } else { inView = true; }
  measure();
  syncControl();
  start();
})();
