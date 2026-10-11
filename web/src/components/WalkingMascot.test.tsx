import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";

import { type Act, WalkingMascot, chooseAct, chooseTarget } from "./WalkingMascot.tsx";

afterEach(cleanup);

describe("chooseAct", () => {
  test("every act can come up, and walking most often", () => {
    const counts: Partial<Record<Act, number>> = {};
    for (let i = 0; i < 1000; i++) {
      const act = chooseAct(i / 1000);
      counts[act] = (counts[act] ?? 0) + 1;
    }
    expect(Object.keys(counts).sort()).toEqual(["dash", "hop", "idle", "look", "spin", "walk", "wiggle"]);
    expect(counts.walk).toBe(420);
    expect(Math.max(...Object.values(counts))).toBe(counts.walk!);
    expect(Object.values(counts).reduce((a, b) => a + b, 0)).toBe(1000);
  });

  test("the ends of the range are handled", () => {
    expect(chooseAct(0)).toBe("walk");
    expect(chooseAct(0.999999)).toBe("spin");
  });
});

describe("chooseTarget", () => {
  test("stays on the buttons and is always a real stroll away", () => {
    const [min, max] = [100, 600];
    for (const from of [100, 180, 350, 520, 600]) {
      for (let i = 0; i < 200; i++) {
        const target = chooseTarget(min, max, from, i / 200);
        expect(target).toBeGreaterThanOrEqual(min);
        expect(target).toBeLessThanOrEqual(max);
        expect(Math.abs(target - from)).toBeGreaterThanOrEqual(47.999);
      }
    }
  });

  test("with no room to stroll it stays put, inside the range", () => {
    expect(chooseTarget(100, 120, 110, 0.9)).toBe(110);
    expect(chooseTarget(100, 120, 300, 0.1)).toBe(120);
    expect(chooseTarget(0, 0, 0, 0.5)).toBe(0);
  });
});

describe("WalkingMascot", () => {
  test("renders the mascot as decoration and survives being unmounted mid-act", () => {
    const view = render(
      <div className="cta">
        <WalkingMascot />
        <a href="#bounties">Start Hunting Today</a>
      </div>,
    );
    const walker = view.container.querySelector(".walker");
    expect(walker?.getAttribute("aria-hidden")).toBe("true");
    const image = view.container.querySelector("img");
    expect(image?.getAttribute("src")).toBe("/app/brand/mascot.png");
    expect(image?.getAttribute("alt")).toBe("");
    (walker as HTMLElement).click();
    view.unmount();
  });
});
