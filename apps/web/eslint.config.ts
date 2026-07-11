import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    files: ["*.config.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.node },
  },
  {
    // T-220: Playwright specs + helpers run under Node, not the browser --
    // same treatment as *.config.ts above, just scoped to e2e/ instead.
    files: ["e2e/**/*.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.strictTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat["recommended-latest"],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { react },
    rules: {
      // react/jsx-no-literals only, not the rest of eslint-plugin-react's
      // recommended set — this project otherwise relies on TypeScript +
      // react-hooks/react-refresh for everything else those rules would
      // cover. §0.11: every user-facing string goes through t(); a bare
      // string literal as a JSX child is the lint-time guard. Attribute
      // values (className, etc.) are deliberately left unchecked —
      // noAttributeStrings defaults to false — since most aren't copy.
      // Genuine exceptions (decorative punctuation, test fixtures) need
      // `// eslint-disable-next-line react/jsx-no-literals -- i18n-ok: <reason>`
      // directly above the line.
      "react/jsx-no-literals": "error",
      // The dangerouslySetInnerHTML ban predates eslint-plugin-react's
      // adoption here and stays as a custom AST selector rather than
      // react/no-danger, to avoid widening this rule's footprint further.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message:
            "dangerouslySetInnerHTML is banned. Sanitize and route through a reviewed component if raw HTML is unavoidable.",
        },
      ],
      // TanStack Router's redirect()/notFound() return a Response/plain
      // object, not an Error — this is the documented shape for control-flow
      // throws in beforeLoad/loader (see e.g. _app.settings.billing.tsx).
      "@typescript-eslint/only-throw-error": [
        "error",
        {
          allow: [
            {
              from: "package",
              package: "@tanstack/router-core",
              name: "Redirect",
            },
            {
              from: "package",
              package: "@tanstack/router-core",
              name: "NotFoundError",
            },
          ],
        },
      ],
    },
  },
  {
    // Route files are HMR-boundaried by @tanstack/router-plugin's own
    // codegen, not by react-refresh's "only export components" convention —
    // every route exports a non-component `Route` alongside its page
    // component, which is the framework's contract, not a fast-refresh hazard.
    files: ["src/routes/**/*.tsx"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
);
