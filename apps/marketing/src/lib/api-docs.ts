/**
 * The public API contract, fetched once at build time (T-314).
 *
 * Same shape and the same reasoning as `src/lib/plans.ts`: `output: "static"`
 * means there is no request-time code that could ask the backend anything, so
 * the document is fetched once during the build and `src/content/api-reference.json`
 * is what the page falls back to. A backend that happens to be restarting
 * during a deploy turns "the API reference is twenty minutes stale" into "the
 * API reference is a 500", which is much worse.
 *
 * **The staleness contract is the same too, with one extra wrinkle.** The
 * committed snapshot is generated from an *all-modules-on* build of the
 * backend (`backend/scripts/dump_openapi.py` plus
 * `app.api.v1.public_openapi.build_public_schema`), because the committed
 * product configuration has `public_api` **off** — so a fetch from a
 * default-configured deployment would 404 and the fallback is the normal
 * case, not the exception. Regenerate the snapshot whenever a route gains or
 * loses a `require_scope`; `tests/api-docs.test.ts` fails the build if the
 * snapshot stops looking like a public contract.
 */
import committed from "../content/api-reference.json";

/** How long the build will wait for the API before falling back. */
const FETCH_TIMEOUT_MS = 5_000;

export type DocSource = "api" | "committed";

export interface ApiOperation {
  /** `GET`, `POST`, … — upper-cased for display. */
  method: string;
  path: string;
  summary: string;
  description: string;
  /** The scope an API key needs, from the document's `x-forge-scope`. */
  scope: string | null;
}

export interface ApiReference {
  title: string;
  version: string;
  /** The document's own `info.description`: authentication, scopes, rate
   *  limits, errors and the versioning policy, written once on the backend
   *  so the page and any other renderer say the same thing. */
  description: string;
  operations: ApiOperation[];
  source: DocSource;
}

interface OpenApiOperation {
  summary?: string;
  description?: string;
  "x-forge-scope"?: string;
}

interface OpenApiDocument {
  info?: { title?: string; version?: string; description?: string };
  paths?: Record<string, Record<string, OpenApiOperation>>;
}

// One fetch per build, not per page: every route under `[...locale]` renders
// once per enabled locale. Same module-level promise as plans.ts, and for
// the same reason — one process builds the whole site, so there is no
// invalidation to get wrong.
let inFlight: Promise<ApiReference> | undefined;

const HTTP_METHODS = new Set(["get", "post", "put", "patch", "delete"]);

function toReference(document: OpenApiDocument, source: DocSource): ApiReference {
  const operations: ApiOperation[] = [];
  for (const [path, methods] of Object.entries(document.paths ?? {})) {
    for (const [method, operation] of Object.entries(methods)) {
      if (!HTTP_METHODS.has(method)) continue;
      operations.push({
        method: method.toUpperCase(),
        path,
        summary: operation.summary ?? "",
        description: operation.description ?? "",
        scope: operation["x-forge-scope"] ?? null,
      });
    }
  }
  // Sorted by path then method so the page's order is stable across builds
  // — an "API reference" whose endpoints reshuffle between deploys is one
  // nobody can link into.
  operations.sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
  return {
    title: document.info?.title ?? "Public API",
    version: document.info?.version ?? "1.0.0",
    description: document.info?.description ?? "",
    operations,
    source,
  };
}

/**
 * Markdown emphasis, removed for a renderer that has none.
 *
 * The document's description is Markdown because that is what an OpenAPI
 * `description` is, and every other renderer of this contract (Swagger UI,
 * Redoc, an integrator's own tooling) will format it. This page cannot: the
 * repo bans `set:html` outright, and shipping a Markdown runtime to a static
 * page for one string is not a trade worth making. So the markers are
 * stripped rather than left to render as literal `**` and backticks, which is
 * what "we support Markdown" looks like when you do not.
 */
export function plainText(markdown: string): string {
  return markdown.replaceAll("**", "").replaceAll("`", "");
}

function isDocument(value: unknown): value is OpenApiDocument {
  return typeof value === "object" && value !== null && "paths" in value;
}

export async function loadApiReference(): Promise<ApiReference> {
  inFlight ??= fetchReference();
  return inFlight;
}

async function fetchReference(): Promise<ApiReference> {
  const origin = import.meta.env.PUBLIC_API_URL;
  if (!origin) {
    return toReference(committed as OpenApiDocument, "committed");
  }

  const url = new URL(
    "api/v1/openapi-public.json",
    origin.endsWith("/") ? origin : `${origin}/`,
  );

  try {
    const response = await fetch(url, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${String(response.status)}`);
    }
    const body: unknown = await response.json();
    if (!isDocument(body)) {
      throw new Error("response was not an OpenAPI document");
    }
    const reference = toReference(body, "api");
    if (reference.operations.length === 0) {
      throw new Error("the document contained no endpoints");
    }
    return reference;
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    console.warn(
      `[marketing] could not fetch the public API document from ${url.href} (${reason}) — ` +
        `building /guides/api from src/content/api-reference.json instead. ` +
        `That snapshot is only as fresh as the last time someone regenerated it.`,
    );
    return toReference(committed as OpenApiDocument, "committed");
  }
}
