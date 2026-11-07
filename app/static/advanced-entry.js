'use strict';
// Intercept before the legacy demo handler: a demo must never replace real data.
document.addEventListener('click', event => {
  if (event.target.closest('#load-demo')) {
    event.preventDefault(); event.stopImmediatePropagation();
    window.location.assign('/?demo=1');
  }
}, true);
