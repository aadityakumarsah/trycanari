import { afterEach, expect, test } from "bun:test";

import { currentTheme, setTheme } from "./theme.ts";

afterEach(() => setTheme("dark"));

test("the page is dark until light is chosen, and the choice is remembered", () => {
  expect(currentTheme()).toBe("dark");
  setTheme("light");
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(currentTheme()).toBe("light");
  expect(localStorage.getItem("trycanari.theme")).toBe("light");
  setTheme("dark");
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(localStorage.getItem("trycanari.theme")).toBe("dark");
});
