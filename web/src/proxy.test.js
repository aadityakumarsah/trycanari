import { describe, expect, test } from "bun:test";

import { isOriginRoute } from "../cloudflare/functions/_middleware.js";

describe("Cloudflare origin routing", () => {
  test("sends server-rendered workspaces and evidence to FastAPI", () => {
    expect(isOriginRoute("/studio/00000000-0000-0000-0000-000000000000")).toBeTrue();
    expect(isOriginRoute("/studio-assets/studio.css")).toBeTrue();
    expect(isOriginRoute("/studio-assets/studio-b1ff1df70.css")).toBeTrue();
    expect(isOriginRoute("/evidence/bounties/a/findings/b")).toBeTrue();
  });

  test("leaves React pages and static assets on Pages", () => {
    expect(isOriginRoute("/b/00000000-0000-0000-0000-000000000000")).toBeFalse();
    expect(isOriginRoute("/app/chunk-example.js")).toBeFalse();
  });
});
