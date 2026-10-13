// Brand artwork is served as files from /app/brand/ (see public/brand), not bundled:
// the page's Content-Security-Policy does not allow images inlined as data: URLs.

/** The trycanari mascot with its name, as it appears in the navigation bar. */
export function Wordmark({ height = 30 }: { height?: number }) {
  // The artwork is 388 x 90.
  const width = Math.round((height * 388) / 90);
  // Two drawings of the same mark, one for each theme. The stylesheet shows the right one.
  return (
    <>
      <img className="wordmark on-dark" src="/app/brand/wordmark.png" alt="trycanari" height={height} width={width} />
      <img className="wordmark on-light" src="/app/brand/wordmark-light.png" alt="trycanari" height={height} width={width} />
    </>
  );
}

const ICONS = {
  x: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM9 9l6 6M15 9l-6 6",
  sun: "M15.500 12a3.500 3.500 0 1 1-7 0 3.500 3.500 0 0 1 7 0zM12 3v2M12 19v2M3 12h2M19 12h2M5.600 5.600l1.400 1.400M17 17l1.400 1.400M5.600 18.400l1.400-1.400M17 7l1.400-1.400",
  moon: "M20 14.500A8 8 0 0 1 9.500 4a8 8 0 1 0 10.500 10.500z",
  bell: "M6 16V11a6 6 0 0 1 12 0v5l1.500 2h-15zM10 20.500a2.200 2.200 0 0 0 4 0",
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
  code: "M8 8l-4 4 4 4M16 8l4 4-4 4M13.500 5l-3 14",
  bot: "M12 3v3M6 6h12v10H6zM3 11v3M21 11v3M9.500 10.500v1M14.500 10.500v1M9 20h6",
  mask: "M4 6c3 1.500 13 1.500 16 0v5c0 4-3.500 7-8 7s-8-3-8-7zM8.500 11h2M13.500 11h2",
  zap: "M13 2L4 14h7l-1 8 9-12h-7z",
  eye: "M2 12s3.500-7 10-7 10 7 10 7-3.500 7-10 7-10-7-10-7zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  check: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM8 12l3 3 5-6",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM15.500 13a3.500 3.500 0 1 1-7 0 3.500 3.500 0 0 1 7 0z",
  user: "M16 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM4.500 20c1.200-3.500 4-5 7.500-5s6.300 1.500 7.500 5",
  globe: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM3 12h18M12 3c2.500 2.700 3.800 5.700 3.800 9s-1.300 6.300-3.800 9c-2.500-2.700-3.800-5.700-3.800-9s1.300-6.300 3.800-9z",
  pin: "M12 21s-6.500-5.600-6.500-10.500a6.500 6.500 0 0 1 13 0C18.500 15.400 12 21 12 21zM14.500 10.500a2.500 2.500 0 1 1-5 0 2.500 2.500 0 0 1 5 0z",
  briefcase: "M4 8h16v11H4zM9 8V5h6v3M4 13h16",
  search: "M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0zM20 20l-4.500-4.500",
  plus: "M12 5v14M5 12h14",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5",
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
