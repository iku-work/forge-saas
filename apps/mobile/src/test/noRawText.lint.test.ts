import { ESLint } from "eslint";
// @ts-expect-error -- no published type declarations for this plugin
import reactNative from "eslint-plugin-react-native";
import { describe, expect, it } from "vitest";

// T-070's CI guard, exercised directly against eslint-plugin-react-native's
// real rule implementation rather than this project's full eslint.config.ts
// -- that config also wires up typescript-eslint's type-aware rules, which
// need a real tsconfig "include" entry and are irrelevant to what this test
// verifies (that react-native/no-raw-text itself does its job). Mirrors
// apps/web/src/test/jsxNoLiterals.lint.test.ts's own approach exactly.
function makeLintGuard(): ESLint {
  return new ESLint({
    overrideConfigFile: true,
    overrideConfig: [
      {
        files: ["**/*.tsx"],
        plugins: { "react-native": reactNative as ESLint.Plugin },
        languageOptions: {
          parserOptions: { ecmaFeatures: { jsx: true }, sourceType: "module" },
        },
        rules: {
          "react-native/no-raw-text": "error",
        },
      },
    ],
  });
}

async function lint(source: string): Promise<ESLint.LintResult[]> {
  return makeLintGuard().lintText(source, { filePath: "LintGuardFixture.tsx" });
}

describe("react-native/no-raw-text lint guard", () => {
  it("fails on a raw string literal outside a <Text> tag", async () => {
    const results = await lint(
      "export function Bad() { return <View>Save</View>; }\n",
    );

    const messages = results.flatMap((result) => result.messages);
    expect(messages.some((m) => m.ruleId === "react-native/no-raw-text")).toBe(
      true,
    );
  });

  it("passes when the same text is wrapped in <Text>", async () => {
    const results = await lint(
      'export function Good({ t }) { return <View><Text>{t("common.button.save")}</Text></View>; }\n',
    );

    const messages = results.flatMap((result) => result.messages);
    expect(messages.some((m) => m.ruleId === "react-native/no-raw-text")).toBe(
      false,
    );
  });
});
