(() => {
  const button = document.querySelector('.gallery-motion');
  const track = document.getElementById('founder-life-track');
  if (button && track) button.addEventListener('click', () => {
    const paused = track.classList.toggle('is-paused');
    button.setAttribute('aria-pressed', String(paused));
    button.textContent = paused ? 'Reprendre le défilement' : 'Mettre en pause';
  });
  const link = document.querySelector('a[href="#parcours-video"]');
  if (link) link.addEventListener('click', () => { document.getElementById('parcours-video').open = true; });
})();
