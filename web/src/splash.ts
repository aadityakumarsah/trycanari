// The loading screen is plain markup in index.html, so it is on screen before any script
// runs. This takes it away once the app has something real to show.

const SHORTEST_MS = 650;
const LONGEST_MS = 9000;
const started = typeof performance === "undefined" ? 0 : performance.now();
let dismissed = false;

/** Fade the loading screen out. Safe to call more than once. */
export function dismissSplash(): void {
  if (dismissed) return;
  dismissed = true;
  const splash = document.getElementById("splash");
  if (!splash) return;
  // Long enough to be seen as a screen rather than a flicker.
  const wait = Math.max(0, SHORTEST_MS - (performance.now() - started));
  setTimeout(() => {
    splash.classList.add("leaving");
    setTimeout(() => splash.remove(), 520);
  }, wait);
}

// Whatever happens to the app, nobody is left staring at the loading screen.
if (typeof window !== "undefined") setTimeout(dismissSplash, LONGEST_MS);
