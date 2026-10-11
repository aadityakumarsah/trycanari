// Development server: hot-reloading pages on :5173, with /v1 proxied to the API.
import index from "./index.html";

const api = process.env.API_URL ?? "http://127.0.0.1:8000";

function proxy(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  // The API checks that cookie-authenticated requests come from its own origin.
  // In development the page is served from here, so present it as the API's own.
  if (headers.has("origin")) headers.set("origin", api);
  headers.delete("host");
  return fetch(`${api}${url.pathname}${url.search}`, {
    method: request.method,
    headers,
    body: request.body,
    redirect: "manual",
  });
}

function fromPublic(request: Request): Response {
  return new Response(Bun.file(`./public${new URL(request.url).pathname.replace(/^\/app/, "")}`));
}

const server = Bun.serve({
  port: Number(process.env.PORT ?? 5173),
  development: true,
  routes: {
    "/v1/*": proxy,
    "/health": proxy,
    "/agent.md": proxy,
    "/docs": proxy,
    "/openapi.json": proxy,
    // In a build these are copied next to the bundle; here they are served from public/.
    "/app/fonts/*": fromPublic,
    "/app/brand/*": fromPublic,
    "/*": index,
  },
});
console.log(`web app on ${server.url} (API: ${api})`);
