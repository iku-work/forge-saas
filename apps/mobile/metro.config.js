const { getDefaultConfig } = require("expo/metro-config");
const { withNativeWind } = require("nativewind/metro");
const path = require("node:path");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "../..");

const config = getDefaultConfig(projectRoot);

// pnpm hoists workspace packages (@forge/*) as symlinks one level up, so
// Metro needs to watch the monorepo root and resolve modules from both
// node_modules locations instead of only the app-local one.
config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];
config.resolver.unstable_enableSymlinks = true;
config.resolver.unstable_enablePackageExports = true;
// Watching the whole workspace root means Metro's resolver also sees
// apps/web and packages/ui|email-templates' Storybook toolchain (vite,
// vitest, storybook) hoisted into the root node_modules -- none of it is
// ever a real dependency of this app, but unstable_enablePackageExports
// resolving a bare "vite" specifier can still walk into it and choke on
// syntax (top-level dynamic import) Metro's static analysis rejects.
//
// *.test.tsx co-located under app/ is expo-router's own recommended
// pattern for keeping a route and its test side by side, but expo-router
// has no built-in awareness of it -- every file under app/ is a route
// candidate, so without this blockList entry Metro tries to bundle each
// test file (and its vitest/@testing-library imports) as if it were a
// screen.
config.resolver.blockList = [
  /apps\/web\/.*/,
  /node_modules\/\.pnpm\/vite@.*/,
  /node_modules\/\.pnpm\/vitest@.*/,
  /.*\.test\.[jt]sx?$/,
];

module.exports = withNativeWind(config, { input: "./global.css" });
