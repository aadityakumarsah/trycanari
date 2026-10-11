// Builds the app into the Python package, where the API serves it under /app/.
import { cp, rm } from "node:fs/promises";

const outdir = "../src/agentbounty/static/app";
await rm(outdir, { recursive: true, force: true });

const result = await Bun.build({
  entrypoints: ["./index.html"],
  outdir,
  minify: true,
  // Absolute, so the hashed assets resolve from any route the browser router owns.
  publicPath: "/app/",
  define: { "process.env.NODE_ENV": '"production"' },
  // Left as URLs: the fonts are copied below, not bundled.
  external: ["/app/fonts/*"],
});

for (const log of result.logs) console.error(log);
if (!result.success) process.exit(1);

// Fonts are copied as files, not bundled. The bundler would inline them into the
// stylesheet as data: URLs, which the page's Content-Security-Policy does not allow.
await cp("./public", outdir, { recursive: true });
for (const output of result.outputs) {
  console.log(`${(output.size / 1024).toFixed(1).padStart(8)} kB  ${output.path.split("/static/app/")[1]}`);
}
