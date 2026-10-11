import { useEffect, useRef } from "react";

/**
 * The mascot, alive: it wanders along the top of the call-to-action buttons and,
 * between walks, does something at random: stands about, hops, looks around,
 * wiggles, spins, or breaks into a dash. Click it and it jumps.
 *
 * It is decoration. It stays still for anyone who has asked for reduced motion,
 * and it stops working while it is scrolled out of view or the tab is hidden.
 */

export type Act = "walk" | "dash" | "idle" | "hop" | "look" | "wiggle" | "spin";

// How often each act is chosen, out of 100.
const WEIGHTS: [Act, number][] = [
  ["walk", 42],
  ["idle", 16],
  ["hop", 12],
  ["look", 10],
  ["wiggle", 8],
  ["dash", 7],
  ["spin", 5],
];
// Pixels a second.
const SPEED: Record<"walk" | "dash", number> = { walk: 55, dash: 190 };
// How long each act that is not a walk lasts, in milliseconds.
const LASTS: Record<Exclude<Act, "walk" | "dash" | "idle">, number> = {
  hop: 620,
  look: 1100,
  wiggle: 900,
  spin: 700,
};
const MIN_STROLL = 48;

/** The next act, given a random number in [0, 1). */
export function chooseAct(random: number): Act {
  let mark = random * 100;
  for (const [act, weight] of WEIGHTS) {
    if (mark < weight) return act;
    mark -= weight;
  }
  return "walk";
}

/**
 * Somewhere to walk to within [min, max], given a random number in [0, 1).
 * It is always a real stroll away when there is room for one, so the mascot never
 * "walks" on the spot.
 */
export function chooseTarget(min: number, max: number, from: number, random: number): number {
  if (max - min < MIN_STROLL) return Math.min(Math.max(from, min), max);
  const target = min + random * (max - min);
  if (Math.abs(target - from) >= MIN_STROLL) return target;
  // Too close: go the same distance the other way, or to whichever end is farther.
  return from - min > max - from ? min + random * (from - min - MIN_STROLL) : from + MIN_STROLL + random * (max - from - MIN_STROLL);
}

const WIDTH = 66;
const HEIGHT = 56;

export function WalkingMascot() {
  const walker = useRef<HTMLSpanElement>(null);
  const facing = useRef<HTMLSpanElement>(null);
  const body = useRef<HTMLImageElement>(null);

  useEffect(() => {
    const root = walker.current;
    const turn = facing.current;
    const figure = body.current;
    const perch = root?.parentElement;
    if (!root || !turn || !figure || !perch) return;

    let min = 0;
    let max = 0;
    let x = 0;
    let frame = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let visible = true;
    let stopped = false;

    /** The stretch it may walk: the top edge of the first row of buttons. */
    function measure() {
      const base = perch!.getBoundingClientRect();
      const buttons = [...perch!.querySelectorAll<HTMLElement>("a, button")].map((b) => b.getBoundingClientRect());
      const top = buttons[0]?.top ?? 0;
      const row = buttons.filter((rect) => Math.abs(rect.top - top) < 4);
      if (row.length === 0) return;
      min = Math.min(...row.map((rect) => rect.left)) - base.left;
      max = Math.max(...row.map((rect) => rect.right)) - base.left - WIDTH;
      place(Math.min(Math.max(x, min), Math.max(min, max)));
    }
    function place(next: number) {
      x = next;
      root!.style.transform = `translate(${x.toFixed(1)}px, -100%)`;
    }
    function face(direction: 1 | -1) {
      turn!.style.transform = direction === 1 ? "" : "scaleX(-1)";
    }
    function pose(name: string) {
      figure!.className = name ? `mascot-body ${name}` : "mascot-body";
    }
    function later(ms: number, then: () => void) {
      timer = setTimeout(() => !stopped && then(), ms);
    }

    function walkTo(target: number, speed: number, pace: string) {
      face(target >= x ? 1 : -1);
      pose(pace);
      let last = performance.now();
      const step = (now: number) => {
        if (stopped) return;
        // A long gap means the tab was in the background: do not leap to make up for it.
        const elapsed = Math.min(now - last, 50) / 1000;
        last = now;
        const distance = target - x;
        const stride = speed * elapsed;
        if (Math.abs(distance) <= stride) {
          place(target);
          pose("");
          later(150 + Math.random() * 400, next);
          return;
        }
        place(x + Math.sign(distance) * stride);
        frame = requestAnimationFrame(step);
      };
      frame = requestAnimationFrame(step);
    }

    function perform(act: Act) {
      if (act === "walk" || act === "dash") {
        walkTo(chooseTarget(min, max, x, Math.random()), SPEED[act], act === "dash" ? "dashing" : "walking");
      } else if (act === "idle") {
        pose("");
        later(700 + Math.random() * 1800, next);
      } else if (act === "look") {
        // Glance over the shoulder, and back.
        pose("");
        const flips = [350, 700, 1050];
        let direction: 1 | -1 = turn!.style.transform ? -1 : 1;
        for (const at of flips.slice(0, 2)) {
          setTimeout(() => {
            if (stopped) return;
            direction = direction === 1 ? -1 : 1;
            face(direction);
          }, at);
        }
        later(LASTS.look, next);
      } else {
        pose(act === "hop" ? "hopping" : act === "wiggle" ? "wiggling" : "spinning");
        later(LASTS[act], () => {
          pose("");
          next();
        });
      }
    }
    function next() {
      if (stopped || !visible) return;
      perform(chooseAct(Math.random()));
    }
    function halt() {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      pose("");
    }

    measure();
    // Start where it used to stand: a little left of centre.
    place(Math.min(Math.max((min + max) / 2 - 16, min), Math.max(min, max)));

    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const resize = new ResizeObserver(measure);
    resize.observe(perch);
    const watch = new IntersectionObserver(([entry]) => {
      const now = entry?.isIntersecting ?? true;
      if (now === visible) return;
      visible = now;
      halt();
      if (visible && !still) later(300, next);
    });
    watch.observe(perch);
    const onClick = () => {
      if (still) return;
      halt();
      perform("hop");
    };
    root.addEventListener("click", onClick);
    if (!still) later(900, next);

    return () => {
      stopped = true;
      halt();
      resize.disconnect();
      watch.disconnect();
      root.removeEventListener("click", onClick);
    };
  }, []);

  return (
    <span ref={walker} className="walker" aria-hidden="true">
      <span ref={facing} className="walker-facing">
        <img ref={body} className="mascot-body" src="/app/brand/mascot.png" alt="" width={WIDTH} height={HEIGHT} />
      </span>
    </span>
  );
}
