import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";

import { advance, Flow } from "./Flow.tsx";

afterEach(cleanup);

describe("advance", () => {
  test("wraps within one copy of the list, in either direction", () => {
    expect(advance(0, 30, 100)).toBe(30);
    expect(advance(90, 30, 100)).toBe(20);
    expect(advance(10, -30, 100)).toBe(80);
    expect(advance(10, -310, 100)).toBe(0);
    expect(advance(50, 10, 0)).toBe(0);
  });
});

describe("Flow", () => {
  test("has two columns a side, running opposite ways and mirrored across the hub", () => {
    const view = render(<Flow />);
    const directions = (side: string) =>
      [...view.container.querySelectorAll(`.flow-wrap-${side} .flow-col`)].map((column) =>
        column.getAttribute("data-direction"),
      );
    expect(directions("left")).toEqual(["1", "-1"]);
    expect(directions("right")).toEqual(["-1", "1"]);
  });

  test("the seamless copy of each list is hidden from screen readers", () => {
    const view = render(<Flow />);
    const lists = [...view.container.querySelectorAll(".flow-col ul")];
    expect(lists.length).toBe(8);
    expect(lists.filter((list) => list.getAttribute("aria-hidden") === "true").length).toBe(4);
    expect(view.getByAltText("The trycanari sandbox")).toBeDefined();
  });
});
