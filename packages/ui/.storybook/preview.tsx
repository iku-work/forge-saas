import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { initialize, mswLoader } from "msw-storybook-addon";
import type { Decorator, Preview } from "@storybook/react-vite";

// The app's own Tailwind entry (design tokens + light/dark variable sets,
// see apps/web/src/index.css) -- reused rather than duplicated so a story
// never drifts from the theme users actually see. `@source` inside that
// file is resolved relative to the CSS file itself, so it still picks up
// packages/ui/src regardless of who imports it.
import "../../../apps/web/src/index.css";
import i18n from "./i18n";

initialize({ onUnhandledRequest: "bypass" });

const withProviders: Decorator = (Story, context) => {
  useEffect(() => {
    document.documentElement.classList.toggle(
      "dark",
      context.globals["theme"] === "dark",
    );
  }, [context.globals]);

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <div className="bg-background p-6 text-foreground">
          <Story />
        </div>
      </I18nextProvider>
    </QueryClientProvider>
  );
};

const preview: Preview = {
  parameters: {
    layout: "centered",
    a11y: {
      // Fails the story (in both the addon panel and storybook:test's CI
      // run) on any violation rather than only warning -- T-150's "axe
      // violations in any story -> test fails" requirement.
      test: "error",
    },
  },
  globalTypes: {
    theme: {
      description: "Light/dark theme",
      toolbar: {
        title: "Theme",
        icon: "mirror",
        items: [
          { value: "light", title: "Light" },
          { value: "dark", title: "Dark" },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: {
    theme: "light",
  },
  decorators: [withProviders],
  loaders: [mswLoader],
};

export default preview;
