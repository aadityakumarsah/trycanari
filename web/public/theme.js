// Applies the chosen theme before the page is drawn, so a light page never opens dark.
// A file of its own because the page's Content-Security-Policy allows no inline script.
(function () {
  try {
    if (localStorage.getItem("trycanari.theme") === "light") {
      document.documentElement.dataset.theme = "light";
    }
  } catch (_) {
    // Storage is switched off: the page stays dark.
  }
})();
