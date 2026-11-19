// Runs before first paint so a saved theme never flashes. External file (no inline script) to keep CSP strict.
(function () {
  try {
    var t = window.localStorage.getItem('cs193t-showcase:theme');
    if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  } catch (e) { /* storage blocked: follow the system setting */ }
})();
