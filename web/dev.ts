// Development: rebuild the app whenever a source file changes.
//
// The API process serves the build (and never caches the page), so there is one
// origin in development exactly as in production: run the API, run this, and
// reload the browser after a save.
import { watch } from "node:fs";

let running = false;
let again = false;

async function build(): Promise<void> {
  if (running) {
    again = true;
    return;
  }
  running = true;
  const started = performance.now();
  const child = Bun.spawn(["bun", "run", "build.ts"], { stdout: "ignore", stderr: "inherit" });
  const code = await child.exited;
  const took = Math.round(performance.now() - started);
  console.log(code === 0 ? `built in ${took} ms` : `build failed (exit ${code})`);
  running = false;
  if (again) {
    again = false;
    await build();
  }
}

await build();
let timer: ReturnType<typeof setTimeout> | undefined;
// public/ is left out: copying it into the build stirs it, and the build would loop.
for (const path of ["src", "index.html"]) {
  watch(path, { recursive: true }, () => {
    clearTimeout(timer);
    timer = setTimeout(() => void build(), 80);
  });
}
console.log("watching web/src for changes; reload the page the API serves");
