import { defineConfig } from "@hey-api/openapi-ts";

/**
 * The input is a *file*, not `http://localhost:8000/openapi.json`, and that is
 * T-313's decision rather than a convenience (see
 * `backend/scripts/dump_openapi.py`, which writes it):
 *
 *  - the document is dumped with **every module enabled**, so the generated
 *    client is the union of every configuration. The SPA gates modules at
 *    runtime from `/api/v1/config`, so one bundle already serves every
 *    product; generating from the committed config meant a default-off
 *    module (`public_api`, `outbound_webhooks`) had no typed symbols to
 *    import, which is what left T-311's last box unticked;
 *  - it needs no port, no database and no Docker, and it works when
 *    `ENVIRONMENT != "dev"` -- where `openapi_url` is deliberately `None`.
 *
 * `pnpm --filter @forge/api-client generate` runs both halves.
 */
export default defineConfig({
  input: "./openapi.json",
  output: { path: "src/generated" },
  plugins: [
    {
      name: "@hey-api/client-fetch",
      runtimeConfigPath: "./src/client.ts",
    },
    "@hey-api/sdk",
    "@tanstack/react-query",
  ],
});
