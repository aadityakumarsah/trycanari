// Builds the app into the Python package, where the API serves it under /app/.
import { rm } from "node:fs/promises";

const outdir = "../src/agentbounty/static/app";
await rm(outdir, { recursive: true, force: true });

const result = await Bun.build({
  entrypoints: ["./index.html"],
  outdir,
  minify: true,
  // Absolute, so the hashed assets resolve from any route the browser router owns.
  publicPath: "/app/",
  define: { "process.env.NODE_ENV": '"production"' },
});

for (const log of result.logs) console.error(log);
if (!result.success) process.exit(1);
for (const output of result.outputs) {
  console.log(`${(output.size / 1024).toFixed(1).padStart(8)} kB  ${output.path.split("/static/app/")[1]}`);
}
