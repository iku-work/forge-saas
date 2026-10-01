import { defineConfig } from "tsup";

export default defineConfig({
  // One entry per component plus code splitting, so dist/index.js re-exports
  // from per-component chunks. With "sideEffects": false in package.json a
  // consumer's bundler then drops the chunks it never imports -- and their
  // dependencies (sonner, cmdk, i18next) with them. A single bundled index.js
  // made every island ship all of it: the marketing pricing island was 227 KB.
  entry: ["src/index.ts", "src/components/!(*.stories).tsx"],
  format: ["esm"],
  splitting: true,
  dts: { entry: "src/index.ts" },
  sourcemap: true,
  clean: true,
});
