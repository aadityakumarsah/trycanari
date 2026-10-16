// Cloudflare Pages middleware: pass API calls to the application server while
// leaving all other requests to Pages' static asset handler.
//
// API_ORIGIN and PROXY_SECRET are Pages production secrets. The latter is shared
// with the API, allowing it to trust the forwarded client address.

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

const PAGE_HEADERS = {
  "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: https://*.googleusercontent.com https://github.com https://avatars.githubusercontent.com https://res.cloudinary.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "Cache-Control": "no-cache",
};

export function isOriginRoute(pathname) {
  return ORIGIN_PATHS.has(pathname) || ORIGIN_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function proxy(request, env, url) {
  if (!env.API_ORIGIN) {
    return Response.json({ detail: "The API is not connected to this site yet." }, { status: 503 });
  }
  const target = new URL(url.pathname + url.search, env.API_ORIGIN);
  const headers = new Headers(request.headers);
  if (headers.has("origin")) headers.set("origin", target.origin);
  headers.set("x-forwarded-host", url.host);
  headers.delete("x-proxy-secret");
  headers.delete("x-client-address");
  if (env.PROXY_SECRET) {
    headers.set("x-proxy-secret", env.PROXY_SECRET);
    headers.set("x-client-address", request.headers.get("cf-connecting-ip") ?? "unknown");
  }
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : request.body;
  return fetch(target, { method: request.method, headers, body, credentials: "include", redirect: "manual" }).then((response) => {
    const forwarded = new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
    const studioAsset = request.method === "GET" &&
      url.pathname.startsWith("/studio-assets/");
    const publicData = request.method === "GET" &&
      (url.pathname === "/v1/providers" || url.pathname.startsWith("/v1/public/"));
    const authenticatedData = url.pathname.startsWith("/v1/account") || url.pathname.startsWith("/v1/sponsor");
    if (studioAsset && response.ok) forwarded.headers.set("cache-control", "public, max-age=31536000, immutable");
    else if (publicData && response.ok) forwarded.headers.set("cache-control", "public, max-age=30, stale-while-revalidate=60");
    else if (authenticatedData) forwarded.headers.set("cache-control", "no-store, no-cache, must-revalidate");
    else if (response.ok) forwarded.headers.set("cache-control", "private, max-age=5");
    return forwarded;
  });
}

export async function onRequest(context) {
  const url = new URL(context.request.url);
  if (isOriginRoute(url.pathname)) return proxy(context.request, context.env, url);

  const response = await context.next();
  const page = new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
  if ((page.headers.get("content-type") ?? "").includes("text/html")) {
    for (const [name, value] of Object.entries(PAGE_HEADERS)) page.headers.set(name, value);
  } else if (/^\/app\/chunk-[^/]+$/.test(url.pathname)) {
    page.headers.set("Cache-Control", "public, max-age=31536000, immutable");
  }
  return page;
}
