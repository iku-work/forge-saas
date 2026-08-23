/**
 * An `astro dev` server, started in a process of its own, for the one T-301
 * assertion that cannot be made against `dist/`: drafts render in development.
 *
 * Two things forced this into a separate file rather than a few lines inside
 * the test.
 *
 * **It cannot run inside vitest.** With `VITEST=true` in the environment, Vite
 * resolves configuration differently and the dev server comes up serving
 * nothing at all -- every route 404s. That looks identical to "the draft was
 * filtered out", which is the thing under test, so an in-process server would
 * have failed for a reason unrelated to the assertion. The test spawns this
 * with vitest's own variables removed.
 *
 * **It cannot be the CLI.** `astro dev` puts itself in the background when it
 * detects an AI-agent environment or is given `--background`, so a spawned CLI
 * exits immediately, ignores `kill()`, and leaves a server running after the
 * test finishes. The Node API hands back a server this process owns.
 *
 * Prints one line of JSON with the port, then stays up until it is killed.
 */
import { dev } from "astro";

const server = await dev({
  root: process.cwd(),
  logLevel: "silent",
  // Port 0: never collide with a dev server the developer already has open.
  server: { port: 0 },
});

process.stdout.write(`${JSON.stringify({ port: server.address.port })}\n`);

// Kept for a clean shutdown where signals are deliverable. On Windows
// `child.kill()` terminates the process outright and this never runs, which is
// fine -- the port goes with it.
for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => {
    void server.stop().then(() => {
      process.exit(0);
    });
  });
}
