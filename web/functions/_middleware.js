// Wrangler discovers Pages Functions from this directory. Keep implementation
// details in cloudflare/ so local Pages and worker tooling use the same proxy.
export { onRequest } from "../cloudflare/functions/_middleware.js";
