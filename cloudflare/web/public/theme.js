(() => {
  const read = () => {
    try {
      return localStorage.getItem('cg-theme') || 'light';
    } catch {
      return 'light';
    }
  };
  const mq = matchMedia('(prefers-color-scheme: dark)');
  const apply = (choice = read()) => {
    document.documentElement.dataset.theme =
      choice === 'system' ? (mq.matches ? 'dark' : 'light') : choice;
  };
  apply();
  mq.addEventListener('change', () => apply());
  document.addEventListener('change', (e) => {
    if (e.target.matches('[data-theme-choice]')) {
      try {
        localStorage.setItem('cg-theme', e.target.value);
      } catch {}
      apply(e.target.value);
    }
  });
  document.addEventListener('click', (e) => {
    const card = e.target.closest('[data-game-choice]');
    if (card) {
      const select = document.querySelector('[data-guide-game]');
      select.value = card.dataset.gameChoice;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      // Rendering is synchronous. Deferring this focus can steal focus from a
      // newly typed custom-game name, especially while the mobile keyboard opens.
      const next =
        document.querySelector('[data-guide-name]') ||
        document.querySelector('[data-game-choice][aria-pressed="true"]');
      next?.focus();
    }
  });
  new MutationObserver(() =>
    document.querySelectorAll('[data-theme-choice]').forEach((s) => {
      if (s.value !== read()) s.value = read();
    }),
  ).observe(document.documentElement, { childList: true, subtree: true });
})();
