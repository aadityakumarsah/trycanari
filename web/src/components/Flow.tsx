import { useEffect, useRef } from "react";

import { Icon, type IconName } from "./Brand.tsx";

type Cell = [IconName, string];

// What goes into the sandbox, and what comes out of it. Two columns a side.
const ATTACKS: Cell[][] = [
  [
    ["chat", "Prompt injection"],
    ["doc", "Poisoned document"],
    ["mask", "Role-play"],
    ["code", "Encoding tricks"],
    ["eye", "Prompt extraction"],
  ],
  [
    ["bot", "Attacker agents"],
    ["layers", "Nested instructions"],
    ["zap", "Urgency and pressure"],
    ["box", "Tool misuse"],
    ["key", "Secret fishing"],
  ],
];

const PROOF: Cell[][] = [
  [
    ["repeat", "Five replays"],
    ["ban", "Canary tripped"],
    ["check", "3-of-5 verdict"],
    ["coin", "Shielded payout"],
    ["clock", "Paid in minutes"],
  ],
  [
    ["doc", "Full transcripts"],
    ["lock", "Pinned config"],
    ["shield", "Mock tools only"],
    ["grid", "Append-only ledger"],
    ["target", "Exact evidence"],
  ],
];

/** Pixels a second a column drifts by itself, and how hard scrolling the page pushes it. */
const DRIFT = 14;
const PUSH = 0.55;

/**
 * Where a column sits after moving `distance` more pixels: always within one
 * copy of its list, so the second copy can take over without a seam.
 */
export function advance(position: number, distance: number, cycle: number): number {
  if (cycle <= 0) return 0;
  return (((position + distance) % cycle) + cycle) % cycle;
}

function Column({ cells, direction }: { cells: Cell[]; direction: 1 | -1 }) {
  // The list is laid out twice so the loop has no seam; the copy is hidden from readers.
  return (
    <div className="flow-col" data-direction={direction}>
      {[false, true].map((copy) => (
        <ul key={String(copy)} aria-hidden={copy || undefined}>
          {cells.map(([icon, label]) => (
            <li key={label} className="flow-cell">
              <Icon name={icon} size={28} />
              <span>{label}</span>
            </li>
          ))}
        </ul>
      ))}
    </div>
  );
}

function Side({ columns, side, label }: { columns: Cell[][]; side: "left" | "right"; label: string }) {
  return (
    <div className={`flow-wrap flow-wrap-${side}`} role="group" aria-label={label}>
      <div className="flow-grid">
        {columns.map((cells, index) => (
          // Neighbouring columns run opposite ways, and the two sides mirror each other.
          <Column key={index} cells={cells} direction={(index % 2 === 0) === (side === "left") ? 1 : -1} />
        ))}
      </div>
    </div>
  );
}

/**
 * Attacks stream in on the left, the sandbox sits in the middle, proof streams
 * out on the right. The columns drift slowly by themselves and are pushed along
 * by scrolling the page: down runs them forward, up runs them back.
 */
export function Flow() {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = root.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const columns = [...element.querySelectorAll<HTMLElement>(".flow-col")].map((column) => ({
      column,
      direction: Number(column.dataset.direction) || 1,
      position: 0,
    }));

    let frame = 0;
    let visible = false;
    let last = performance.now();
    let scrolled = window.scrollY;
    let momentum = 0;

    const step = (now: number) => {
      const seconds = Math.min(0.05, (now - last) / 1000);
      last = now;
      // What the page scrolled since the last frame becomes momentum that fades out.
      momentum += (window.scrollY - scrolled) * PUSH;
      scrolled = window.scrollY;
      const pushed = momentum * Math.min(1, seconds * 9);
      momentum -= pushed;
      const hovering = element.matches(":hover");
      const drift = still || hovering ? 0 : DRIFT * seconds;

      for (const item of columns) {
        const cycle = (item.column.firstElementChild as HTMLElement | null)?.offsetHeight ?? 0;
        item.position = advance(item.position, (drift + (still ? 0 : pushed)) * item.direction, cycle);
        item.column.style.transform = `translate3d(0, ${-item.position}px, 0)`;
      }
      // The hub leans into a hard scroll, then settles.
      element.style.setProperty("--lean", String(Math.max(-1, Math.min(1, momentum / 60))));
      if (visible) frame = requestAnimationFrame(step);
    };

    const watcher = new IntersectionObserver(([entry]) => {
      const now = entry?.isIntersecting ?? false;
      if (now && !visible) {
        last = performance.now();
        scrolled = window.scrollY;
        frame = requestAnimationFrame(step);
      }
      if (!now) cancelAnimationFrame(frame);
      visible = now;
    });
    watcher.observe(element);
    return () => {
      watcher.disconnect();
      cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div className="flow" ref={root}>
      <div className="flow-bg" aria-hidden="true" />
      <Side columns={ATTACKS} side="left" label="What hunters try" />
      <span className="flow-line flow-line-left" aria-hidden="true" />
      <span className="flow-line flow-line-right" aria-hidden="true" />
      <div className="flow-hub">
        <img src="/app/brand/mascot.png" alt="The trycanari sandbox" width={76} height={76} />
      </div>
      <Side columns={PROOF} side="right" label="What comes out" />
    </div>
  );
}
