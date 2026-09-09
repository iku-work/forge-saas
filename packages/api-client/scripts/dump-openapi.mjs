/**
 * Runs `backend/scripts/dump_openapi.py` and leaves the document at
 * `packages/api-client/openapi.json`, which is what `openapi-ts.config.ts`
 * reads (T-313).
 *
 * The whole of this file is "find a Python that has the backend's
 * dependencies installed". That sounds like it should not need code, and it
 * does: `python` is the Windows name and `python3` the POSIX one, a repo
 * checked out with a virtualenv under `backend/.venv` has a third answer
 * that is on nobody's PATH, and the previous arrangement dodged the question
 * only by requiring a *running* backend (and, for `make gen-client`, Docker)
 * instead. Preferring the venv means a developer who set the backend up the
 * way TEMPLATE.md says never has to think about which interpreter `pnpm`
 * inherited.
 *
 * Exit codes are forwarded, and a failure prints the interpreters that were
 * tried -- "openapi.json not found" three steps later is a much worse
 * message than "no Python with the backend installed".
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..", "..");
const script = path.join(repoRoot, "backend", "scripts", "dump_openapi.py");

const venvCandidates = [
  path.join(repoRoot, "backend", ".venv", "Scripts", "python.exe"),
  path.join(repoRoot, "backend", ".venv", "bin", "python"),
];

const candidates = [
  ...venvCandidates.filter((candidate) => existsSync(candidate)),
  process.env.PYTHON,
  "python",
  "python3",
].filter((candidate) => typeof candidate === "string" && candidate.length > 0);

let lastStatus = 1;
for (const python of candidates) {
  const result = spawnSync(python, [script], { stdio: "inherit" });
  // ENOENT means "no such interpreter" -- keep looking. Any other outcome is
  // this interpreter's answer, success or failure, and re-running the dump
  // under a different Python would only hide it.
  if (result.error && result.error.code === "ENOENT") {
    continue;
  }
  if (result.error) {
    console.error(`[api-client] ${python}: ${result.error.message}`);
    process.exit(1);
  }
  lastStatus = result.status ?? 1;
  process.exit(lastStatus);
}

console.error(
  `[api-client] no Python interpreter with the backend installed was found.\n` +
    `Tried: ${candidates.join(", ")}\n` +
    `Install the backend (cd backend && pip install -e ".[dev]") or set PYTHON=<path>.`,
);
process.exit(lastStatus);
