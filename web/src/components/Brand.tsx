// Brand artwork is served as files from /app/brand/ (see public/brand), not bundled:
// the page's Content-Security-Policy does not allow images inlined as data: URLs.

/** The trycanari mascot with its name, as it appears in the navigation bar. */
export function Wordmark({ height = 30 }: { height?: number }) {
  // The artwork is 388 x 90.
  return (
    <img
      className="wordmark"
      src="/app/brand/wordmark.png"
      alt="trycanari"
      height={height}
      width={Math.round((height * 388) / 90)}
    />
  );
}

const ICONS = {
  ban: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM5.6 5.6l12.8 12.8",
  box: "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10",
  chat: "M4 5h16v11H9l-5 4z",
  chevron: "M9 6l6 6-6 6",
  coin: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM9 9h6l-6 6h6",
  doc: "M7 3h7l4 4v14H7zM14 3v4h4",
  down: "M6 9l6 6 6-6",
  github:
    "M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.4 5.4 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65S8.930 17.380 9 18v4M9 18c-4.510 2-5-2-7-2",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  search: "M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0zM20 20l-4.500-4.500",
  plus: "M12 5v14M5 12h14",
  close: "M6 6l12 12M18 6L6 18",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 7v5l3 2",
  link: "M10 14a4 4 0 0 0 5.660 0l3-3a4 4 0 0 0-5.660-5.660l-1 1M14 10a4 4 0 0 0-5.660 0l-3 3a4 4 0 0 0 5.660 5.660l1-1",
  shield: "M12 3l8 3v6c0 4.500-3.200 8-8 9-4.800-1-8-4.500-8-9V6z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  key: "M12 15a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM11 12l9-9M17 6l3 3M14 9l2 2",
  lock: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
  menu: "M4 7h16M4 12h16M4 17h16",
  repeat: "M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4",
  target: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  up: "M6 15l6-6 6 6",
} as const;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICONS[name]} />
    </svg>
  );
}
