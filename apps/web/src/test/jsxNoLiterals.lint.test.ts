import { ESLint } from "eslint";
import react from "eslint-plugin-react";
import { describe, expect, it } from "vitest";

// §0.11's CI guard, exercised directly against eslint-plugin-react's real
// rule implementation rather than this project's full eslint.config.ts --
// that config also wires up typescript-eslint's type-aware rules, which
// need a real tsconfig "include" entry and are irrelevant to what this test
// is actually verifying (that react/jsx-no-literals itself does its job).
function makeLintGuard(): ESLint {
  return new ESLint({
    overrideConfigFile: true,
    overrideConfig: [
      {
        files: ["**/*.tsx"],
        plugins: { react },
        languageOptions: {
          parserOptions: { ecmaFeatures: { jsx: true }, sourceType: "module" },
        },
        rules: {
          "react/jsx-no-literals": "error",
        },
      },
    ],
  });
}

async function lint(source: string): Promise<ESLint.LintResult[]> {
  return makeLintGuard().lintText(source, { filePath: "LintGuardFixture.tsx" });
}

describe("react/jsx-no-literals lint guard", () => {
  it("fails on a bare string literal as a JSX child", async () => {
    const results = await lint(
      "export function Bad() { return <button>Save</button>; }\n",
    );

    const messages = results.flatMap((result) => result.messages);
    expect(messages.some((m) => m.ruleId === "react/jsx-no-literals")).toBe(
      true,
    );
  });

  it("passes when the text comes from a t() call instead", async () => {
    const results = await lint(
      'export function Good({ t }) { return <button>{t("common.button.save")}</button>; }\n',
    );

    const messages = results.flatMap((result) => result.messages);
    expect(messages.some((m) => m.ruleId === "react/jsx-no-literals")).toBe(
      false,
    );
  });
});
