// Publishes the app to Cloudflare Pages: `bun run deploy [project]`.
//
// Pages serves the static build and cloudflare/_worker.js passes API paths on to
// the API (set its address once with `wrangler pages secret put API_ORIGIN`).
import { cp, rm } from "node:fs/promises";

const project = process.argv[2] ?? "trycanari";
const built = "../src/agentbounty/static/app";
const site = "./.pages";

async function run(command: string[]): Promise<void> {
  const code = await Bun.spawn(command, { stdout: "inherit", stderr: "inherit" }).exited;
  if (code !== 0) process.exit(code);
}

await run(["bun", "run", "build.ts"]);
await rm(site, { recursive: true, force: true });
// The build names its files /app/..., so they sit in app/ and the page at the root.
await cp(built, `${site}/app`, { recursive: true });
await Bun.write(`${site}/index.html`, Bun.file(`${site}/app/index.html`));
await rm(`${site}/app/index.html`);
await Bun.write(`${site}/_worker.js`, Bun.file("./cloudflare/_worker.js"));
// wrangler 3 is the newest that runs on Node 20.
await run(["bunx", "wrangler@3", "pages", "deploy", site, "--project-name", project, "--branch", "main", "--commit-dirty=true"]);
