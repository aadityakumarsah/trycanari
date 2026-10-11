// Cloudflare Pages entry point: serves the built app, and passes API paths to the
// API so the browser sees one origin, exactly as when FastAPI serves the app itself.
//
// API_ORIGIN (a Pages secret) is where the API runs, such as https://api.example.com.

const API_PREFIXES = ["/v1/"];
const API_PATHS = new Set(["/v1", "/health", "/agent.md", "/api-docs", "/openapi.json"]);

// The same headers the API puts on the page (see src/agentbounty/api/spa.py).
const PAGE_HEADERS = {
  "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "Cache-Control": "no-cache",
};

function isApi(pathname) {
  return API_PATHS.has(pathname) || API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
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

function proxy(request, env, url) {
  if (!env.API_ORIGIN) {
    return Response.json({ detail: "The API is not connected to this site yet." }, { status: 503 });
  }
  const target = new URL(url.pathname + url.search, env.API_ORIGIN);
  const headers = new Headers(request.headers);
  // The API checks that cookie-authenticated requests come from its own origin. The
  // browser's own verdict (Sec-Fetch-Site) is passed on untouched.
  if (headers.has("origin")) headers.set("origin", target.origin);
  headers.set("x-forwarded-host", url.host);
  return fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    redirect: "manual",
  });
}

export default {
  fetch(request, env) {
    const url = new URL(request.url);
    return isApi(url.pathname) ? proxy(request, env, url) : page(request, env);
  },
};
