import { describe, expect, test } from "bun:test";

import { dismissSplash } from "./splash.ts";

describe("the loading screen", () => {
  test("fades out, is removed, and going again is harmless", async () => {
    const splash = document.createElement("div");
    splash.id = "splash";
    document.body.append(splash);

    dismissSplash();
    dismissSplash();
    expect(document.getElementById("splash")).not.toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 760));
    expect(splash.classList.contains("leaving")).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(document.getElementById("splash")).toBeNull();
    dismissSplash();
  });
});
