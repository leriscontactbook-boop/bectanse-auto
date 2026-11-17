(() => {
  'use strict';
  // Keep server-delivered and dynamically inserted interface text free of emoji.
  const symbols = /[\p{Extended_Pictographic}\p{Regional_Indicator}\u{1F3FB}-\u{1F3FF}\u2190-\u21ff\u2600-\u27bf\uFE0E\uFE0F\u200D\u20E3]/gu;
  const labels = new Map([[String.fromCodePoint(0x1f44d), 'Utile'], [String.fromCodePoint(0x2764,0xfe0f), 'Soutien'], [String.fromCodePoint(0x1f525), 'Excellent'], [String.fromCodePoint(0x1f44f), 'Bravo'], [String.fromCodePoint(0x1f4af), 'D’accord'], [String.fromCodePoint(0x1f64f), 'Merci']]);
  const clean = value => value.replace(symbols, '');
  const excluded = node => node.parentElement?.closest('script,style,textarea,[contenteditable="true"],svg,#chat-feed,#canal-preview-list,[data-reactions]');
  function text(node) {
    if (excluded(node)) return;
    let next = clean(node.data);
    if (next === node.data) return;
    const control = node.parentElement?.closest('button,a');
    if (!next.trim() && control && !clean(control.textContent).trim()) {
      next = labels.get(node.data.trim()) || clean(control.getAttribute('aria-label') || control.getAttribute('title') || 'Ouvrir');
    }
    node.data = next;
  }
  function walk(root) {
    if (root.nodeType === 3) { text(root); return; }
    if (root.nodeType !== 1 || root.closest('script,style,textarea,svg,[contenteditable="true"],#chat-feed,#canal-preview-list,[data-reactions]')) return;
    for (const name of ['aria-label','title','placeholder','alt']) {
      const old = root.getAttribute(name);
      if (old && old !== clean(old)) root.setAttribute(name, clean(old));
    }
    for (const child of [...root.childNodes]) walk(child);
  }
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'characterData' || record.type === 'attributes') walk(record.target);
      else for (const node of record.addedNodes) walk(node);
    }
  }).observe(document.documentElement, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:['aria-label','title','placeholder','alt']});
  walk(document.documentElement);
})();
