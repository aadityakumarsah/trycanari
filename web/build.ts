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
  // Left as URLs: fonts and brand artwork are copied below, not bundled.
  external: ["/app/fonts/*", "/app/brand/*"],
});

for (const log of result.logs) console.error(log);
if (!result.success) process.exit(1);

// Fonts and brand artwork are copied as files, not bundled. The bundler can inline
// assets as data: URLs, which the page's Content-Security-Policy does not allow.
await cp("./public", outdir, { recursive: true });
// The tab icon is linked here rather than in index.html: the bundler tries to resolve
// every URL in the HTML it is given, and these files are copied, not bundled.
const shell = `${outdir}/index.html`;
const icons =
  '<link rel="icon" href="/app/brand/favicon.png" type="image/png" />' +
  '<link rel="apple-touch-icon" href="/app/brand/apple-touch-icon.png" />';
await Bun.write(shell, (await Bun.file(shell).text()).replace("</head>", `${icons}</head>`));

for (const output of result.outputs) {
  console.log(`${(output.size / 1024).toFixed(1).padStart(8)} kB  ${output.path.split("/static/app/")[1]}`);
}
