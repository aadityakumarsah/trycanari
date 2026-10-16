import { afterEach, describe, expect, mock, test } from "bun:test";

import { ApiError, api, errorDetail, messageOf } from "./api.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function answer(status: number, body: unknown, headers?: HeadersInit) {
  const init: ResponseInit = headers === undefined ? { status } : { status, headers };
  const fake = mock(async (_url: string | URL | Request, _init?: RequestInit) =>
    new Response(body === undefined ? "<html>" : JSON.stringify(body), init),
  );
  globalThis.fetch = fake as unknown as typeof fetch;
  return fake;
}

describe("api", () => {
  test("sends JSON with the session cookie and returns the body", async () => {
    const fake = answer(201, { id: "b1" });
    const created = await api<{ id: string }>("POST", "/v1/bounties", { body: { reward_zatoshi: 5 } });
    expect(created).toEqual({ id: "b1" });

    const [url, init] = fake.mock.calls[0]!;
    expect(url).toBe("/v1/bounties");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("same-origin");
    expect(init?.body).toBe('{"reward_zatoshi":5}');
    const headers = init?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    // A sponsor's key is never sent from script: the cookie carries the session.
    expect(headers.Authorization).toBeUndefined();
  });

  test("sends a hunter's status token as a bearer token, not in the URL", async () => {
    const fake = answer(200, { state: "queued" });
    await api("GET", "/v1/attempts/a1", { token: "tok-123" });
    const [url, init] = fake.mock.calls[0]!;
    expect(String(url)).not.toContain("tok-123");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-123");
    expect(init?.body).toBeUndefined();
  });

  test("turns a refusal into an ApiError with the API's own words", async () => {
    answer(409, { detail: "the bounty is paused and is not accepting attempts" });
    const refusal = api("POST", "/v1/bounties/b1/attempts", { body: {} });
    expect(refusal).rejects.toBeInstanceOf(ApiError);
    await refusal.catch((error: ApiError) => {
      expect(error.status).toBe(409);
      expect(error.message).toBe("the bounty is paused and is not accepting attempts");
    });
  });

  test("explains a rate limit and survives a body that is not JSON", async () => {
    answer(429, undefined, { "Retry-After": "7" });
    await api("GET", "/v1/bounties").catch((error: ApiError) => {
      expect(error.status).toBe(429);
      expect(error.message).toContain("Too many requests");
      expect(error.retryAfterMs).toBe(7000);
    });
  });
});

describe("errorDetail", () => {
  test("reads validation errors as one message per field", () => {
    const body = {
      detail: [
        { loc: ["body", "reward_zatoshi"], msg: "Input should be greater than 0" },
        { loc: ["body", "fills", 0, "content"], msg: "String should have at least 1 character" },
        { msg: "Value error" },
      ],
    };
    expect(errorDetail(body, "x")).toBe(
      "reward_zatoshi: Input should be greater than 0; fills.0.content: String should have at least 1 character; Value error",
    );
  });

  test("falls back when there is nothing usable", () => {
    for (const body of [null, {}, { detail: "" }, { detail: 5 }]) {
      expect(errorDetail(body, "Request failed (500).")).toBe("Request failed (500).");
    }
  });
});

describe("messageOf", () => {
  test("shows API errors as given and network failures in plain words", () => {
    expect(messageOf(new ApiError(404, "attempt not found"))).toBe("attempt not found");
    expect(messageOf(new TypeError("Failed to fetch"))).toContain("could not be reached");
    const aborted = new Error("aborted");
    aborted.name = "AbortError";
    expect(messageOf(aborted)).toBe("");
  });
});
