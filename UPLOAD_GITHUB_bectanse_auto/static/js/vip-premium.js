(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion || !('IntersectionObserver' in window)) return;

  document.documentElement.classList.add('reveal-ready');
  var selectors = [
    '.section-head', '.capture', '.feature', '.resource-card', '.story-photo',
    '.story-copy', '.step', '.video-copy', '.video-frame', '.explorer-copy',
    '.app-panel', '.academy-value-head', '.training-book', '.included-panel',
    '.proof-intro', '.telegram-message', '.audio-card', '.decision-head',
    '.decision-table', '.offer', '.faq-item', '.final'
  ];
  var nodes = Array.prototype.slice.call(document.querySelectorAll(selectors.join(',')));
  nodes.forEach(function (node, index) {
    node.classList.add('premium-reveal');
    node.style.transitionDelay = Math.min(index % 4, 3) * 55 + 'ms';
  });

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: .08 });

  nodes.forEach(function (node) { observer.observe(node); });
})();
