/** The mark and wordmark. */
export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path fill="#fff" d="M3 2h18v10l-9 10-9-10z" />
      <path fill="#000" d="M7 6h10v2H9v3h6v2H9v3l-2-2z" />
    </svg>
  );
}

// The pixel-art canary. Each character is one square.
const PIXELS = [
  "................",
  ".....ggww.......",
  "....gggww.......",
  "...mmmmmmmm.....",
  "..mmmmmmmmmm....",
  "..mmkmmmmkmmyy..",
  "..mmkmmmmkmmyyy.",
  "..mmmmmmmmmmyy..",
  ".dmmmmmmmmmm....",
  ".ddmmmmmmmmm....",
  "..ddmmmmmmm.....",
  "....dd..dd......",
  "....d...d.......",
  "...dd..dd.......",
];
const COLOURS: Record<string, string> = {
  m: "#6ee7b7",
  d: "#3f9f7c",
  k: "#000",
  w: "#fff",
  g: "#a3a3a3",
  y: "#f4b728",
};

export function Mascot() {
  return (
    <svg className="mascot" viewBox="0 0 16 14" width={64} height={56} shapeRendering="crispEdges" aria-hidden="true">
      {PIXELS.flatMap((line, y) =>
        [...line].map((character, x) =>
          character === "." ? null : (
            <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={COLOURS[character]} />
          ),
        ),
      )}
    </svg>
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
