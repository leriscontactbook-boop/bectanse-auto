(function () {
  'use strict';

  const isEditable = (target) => Boolean(
    target && target.closest && target.closest('input, textarea, select, [contenteditable="true"]')
  );

  document.addEventListener('contextmenu', (event) => {
    if (!isEditable(event.target)) {
      event.preventDefault();
    }
  }, { capture: true });

  document.addEventListener('keydown', (event) => {
    const key = String(event.key || '').toUpperCase();
    const commandKey = event.ctrlKey || event.metaKey;
    const developerShortcut = (
      event.key === 'F12' ||
      (commandKey && event.shiftKey && ['I', 'J', 'C'].includes(key)) ||
      (commandKey && key === 'U')
    );

    if (developerShortcut) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, { capture: true });
})();
