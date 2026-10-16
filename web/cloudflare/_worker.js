// Cloudflare Pages entry point: serves the built app, and passes API paths to the
// API so the browser sees one origin, exactly as when FastAPI serves the app itself.
//
// API_ORIGIN (a Pages secret) is where the API runs, such as https://api.example.com.
// PROXY_SECRET (a Pages secret, the same value as the API's) lets the API trust the
// client address this worker reports.

const ORIGIN_PREFIXES = ["/v1/", "/studio/", "/studio-assets/", "/evidence/"];
const ORIGIN_PATHS = new Set([
  "/v1",
  "/studio",
  "/evidence",
  "/health",
  "/agent.md",
  "/skill.md",
  "/api-docs",
  "/openapi.json",
]);

// The same headers the API puts on the page (see src/agentbounty/api/spa.py).
const PAGE_HEADERS = {
  "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: https://*.googleusercontent.com https://github.com https://avatars.githubusercontent.com https://res.cloudinary.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "Cache-Control": "no-cache",
};

function isOriginRoute(pathname) {
  return ORIGIN_PATHS.has(pathname) || ORIGIN_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

async function page(request, env) {
  const found = await env.ASSETS.fetch(request);
  const response = new Response(found.body, found);
  if ((response.headers.get("content-type") ?? "").includes("text/html")) {
    for (const [name, value] of Object.entries(PAGE_HEADERS)) response.headers.set(name, value);
  } else if (/^\/app\/chunk-[^/]+$/.test(new URL(request.url).pathname)) {
    // Hashed build output: safe to cache for good.
    response.headers.set("Cache-Control", "public, max-age=31536000, immutable");
  }
  return response;
}

async function proxy(request, env, url) {
  if (!env.API_ORIGIN) {
    return Response.json({ detail: "The API is not connected to this site yet." }, { status: 503 });
  }
  const target = new URL(url.pathname + url.search, env.API_ORIGIN);
  const headers = new Headers(request.headers);
  // The API checks that cookie-authenticated requests come from its own origin. The
  // browser's own verdict (Sec-Fetch-Site) is passed on untouched.
  if (headers.has("origin")) headers.set("origin", target.origin);
  headers.set("x-forwarded-host", url.host);
  // Tell the API who is really calling, so its per-client limit is per person and not
  // one allowance for this whole site. The API believes it only with the shared secret.
  headers.delete("x-proxy-secret");
  headers.delete("x-client-address");
  if (env.PROXY_SECRET) {
    headers.set("x-proxy-secret", env.PROXY_SECRET);
    headers.set("x-client-address", request.headers.get("cf-connecting-ip") ?? "unknown");
  }
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : request.body;
  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
    });
    // Add smart caching headers for public data
    const newResponse = new Response(response.body, response);
    const isPublicData = request.method === "GET" &&
      (url.pathname === "/v1/auth/providers" ||
       url.pathname === "/v1/bounties" ||
       url.pathname === "/v1/activity" ||
       url.pathname === "/v1/leaderboard");

    if (isPublicData && response.ok) {
      // Keep public data very fresh, but permit a known-good response during a brief
      // origin restart instead of replacing the page with an error.
      newResponse.headers.set(
        "cache-control",
        "public, max-age=5, stale-while-revalidate=15, stale-if-error=300",
      );
    } else {
      // Session and account data, and every mutation, must never be reused.
      newResponse.headers.set("cache-control", "no-store, no-cache, must-revalidate");
    }

    // Forward rate limit headers from API
    const rateLimit = response.headers.get("X-RateLimit-Limit");
    const rateRemaining = response.headers.get("X-RateLimit-Remaining");
    const rateReset = response.headers.get("X-RateLimit-Reset");
    if (rateLimit) newResponse.headers.set("X-RateLimit-Limit", rateLimit);
    if (rateRemaining) newResponse.headers.set("X-RateLimit-Remaining", rateRemaining);
    if (rateReset) newResponse.headers.set("X-RateLimit-Reset", rateReset);

    return newResponse;
  } catch (_error) {
    return Response.json(
      { detail: "The API is waking up. This page will retry automatically." },
      { status: 503, headers: { "Retry-After": "2", "Cache-Control": "no-store" } },
    );
  }
}

export default {
  fetch(request, env) {
    const url = new URL(request.url);
    return isOriginRoute(url.pathname) ? proxy(request, env, url) : page(request, env);
  },
};
