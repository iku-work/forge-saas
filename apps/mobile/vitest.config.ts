import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: {
    // react-native-web's own source needs the automatic runtime; without
    // this esbuild leaves bare `<Tag />` JSX in place and expects a global
    // `React` that nothing here provides.
    jsx: "automatic",
  },
  resolve: {
    alias: {
      // The real react-native package's entry point uses Flow's `import
      // typeof` syntax, which Vite's Rollup-based transform can't parse --
      // there is no Metro here to strip it. react-native-web is plain
      // JS/JSX and is what Expo itself bundles for the web platform target,
      // so screens render through the same components in tests as they do
      // on a device; only true native modules (expo-secure-store,
      // expo-router's own navigation internals) still need mocking.
      "react-native": "react-native-web",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // setup.ts's beforeAll imports the whole i18n bundle (every locale). Alone
    // that takes a second or two; under a full parallel `pnpm test`, with
    // every package's suite competing for the CPU, it overran vitest's 10s
    // default and four files failed before a single test ran.
    hookTimeout: 30000,
  },
});
