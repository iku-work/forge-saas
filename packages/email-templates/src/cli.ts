/**
 * Invoked by backend/app/workers/jobs/email.py as a bare `node` subprocess --
 * the Python worker image ships no pnpm/node_modules, so this file is built
 * (see tsup.config.ts) into a single dependency-free bundle.
 *
 * Usage: node cli.js <template_name> <json_payload>
 * json_payload shape: RenderEmailInput (see render.ts)
 * stdout (on success): JSON-encoded RenderedEmail, i.e. {"html": "...", "text": "..."}
 * stderr + exit code 1 (on failure): a single error message line
 */
import { renderEmail } from "./render.js";
import type { RenderEmailInput } from "./render.js";

async function main(): Promise<void> {
  const [templateName, payloadJson] = process.argv.slice(2);
  if (!templateName || !payloadJson) {
    throw new Error("usage: node cli.js <template_name> <json_payload>");
  }
  const input = JSON.parse(payloadJson) as RenderEmailInput;
  const result = await renderEmail(templateName, input);
  process.stdout.write(JSON.stringify(result));
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
