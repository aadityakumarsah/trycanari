// The page is dark unless its reader chose light. The choice is kept by this browser and
// applied before the first paint by public/theme.js; this module is how the app changes it.
import { useEffect, useState } from "react";

export type Theme = "dark" | "light";

const KEY = "trycanari.theme";
const CHANGED = "trycanari:theme";

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function setTheme(theme: Theme): void {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Not remembered, then. The page still changes.
  }
  window.dispatchEvent(new Event(CHANGED));
}

/** The theme in use, and a way to flip it. Every use of the hook stays in step. */
export function useTheme(): [Theme, () => void] {
  const [theme, set] = useState<Theme>(currentTheme);
  useEffect(() => {
    const sync = () => set(currentTheme());
    window.addEventListener(CHANGED, sync);
    return () => window.removeEventListener(CHANGED, sync);
  }, []);
  return [theme, () => setTheme(theme === "light" ? "dark" : "light")];
}
