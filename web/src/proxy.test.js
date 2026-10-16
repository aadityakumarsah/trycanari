import { describe, expect, test } from "bun:test";

import { isCacheableStudioAsset, isOriginRoute } from "../cloudflare/functions/_middleware.js";

describe("Cloudflare origin routing", () => {
  test("sends server-rendered workspaces and evidence to FastAPI", () => {
    expect(isOriginRoute("/studio/00000000-0000-0000-0000-000000000000")).toBeTrue();
    expect(isOriginRoute("/studio-assets/studio.css")).toBeTrue();
    expect(isOriginRoute("/studio-assets/studio-b1ff1df70-v2.css")).toBeTrue();
    expect(isOriginRoute("/evidence/bounties/a/findings/b")).toBeTrue();
  });

  test("only caches a studio asset when the origin really returned CSS", () => {
    const css = new Response("body {}", { headers: { "content-type": "text/css; charset=utf-8" } });
    const html = new Response("<html></html>", { headers: { "content-type": "text/html" } });
    expect(isCacheableStudioAsset("GET", "/studio-assets/studio-b1ff1df70-v2.css", css)).toBeTrue();
    expect(isCacheableStudioAsset("GET", "/studio-assets/studio-b1ff1df70-v2.css", html)).toBeFalse();
    expect(isCacheableStudioAsset("GET", "/studio-assets/missing.css", new Response("", { status: 404 }))).toBeFalse();
  });

  test("leaves React pages and static assets on Pages", () => {
    expect(isOriginRoute("/b/00000000-0000-0000-0000-000000000000")).toBeFalse();
    expect(isOriginRoute("/app/chunk-example.js")).toBeFalse();
  });
});
