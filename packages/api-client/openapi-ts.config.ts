import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "http://localhost:8000/openapi.json",
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
