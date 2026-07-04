import tailwindcss from "@tailwindcss/vite";
import type { StorybookConfig } from "@storybook/react-vite";

const config: StorybookConfig = {
  stories: [
    "../src/**/*.stories.@(ts|tsx)",
    // NotificationBell (T-104) lives in apps/web, not packages/ui: it's
    // wired to app-only concerns (the SSE transport in
    // apps/web/src/lib/sse.ts, the router) that don't belong in a shared,
    // headless component package. T-150 still wants it covered by this same
    // Storybook instance rather than standing up a second one for one
    // component -- its own relative imports resolve normally since the
    // story file stays put next to the component.
    "../../../apps/web/src/features/notifications/*.stories.@(ts|tsx)",
  ],
  addons: ["@storybook/addon-a11y"],
  framework: {
    name: "@storybook/react-vite",
    options: {},
  },
  staticDirs: ["../public"],
  async viteFinal(viteConfig) {
    const { mergeConfig } = await import("vite");
    return mergeConfig(viteConfig, {
      plugins: [tailwindcss()],
    });
  },
};

export default config;
