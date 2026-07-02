import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
// @ts-expect-error -- no published type declarations for this plugin
import reactNative from "eslint-plugin-react-native";
// @ts-expect-error -- no published type declarations for this plugin
import reactNativeA11y from "eslint-plugin-react-native-a11y";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", ".expo", "expo-env.d.ts"] },
  {
    files: ["*.config.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["app/**/*.{ts,tsx}", "src/**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.strictTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat["recommended-latest"],
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.node, __DEV__: "readonly" },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-native-a11y": reactNativeA11y,
      "react-native": reactNative,
    },
    rules: {
      ...reactNativeA11y.configs.all.rules,
      // T-070: a string literal rendered as a JSX child outside a <Text>
      // (or TSpan/StyledText/Animated.Text, which the rule also allows by
      // default) crashes on a real device -- React Native requires raw text
      // to live inside <Text>. Catches the structural mistake; the i18n
      // retrofit itself (every <Text> child going through t()) is enforced
      // by the manual pass described in agents.md's T-070, not by this rule.
      "react-native/no-raw-text": "error",
      // The shipped plugin enforces accessibilityRole/-state/-value etc. but
      // has no rule for the most basic case, so it's added directly here:
      // every Pressable needs a label for screen readers to announce it.
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "JSXOpeningElement[name.name='Pressable']:not(:has(JSXAttribute[name.name='accessibilityLabel']))",
          message:
            "Pressable must have an accessibilityLabel prop for screen readers.",
        },
      ],
    },
  },
);
