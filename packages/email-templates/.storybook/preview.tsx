import type { Preview } from "@storybook/react-vite";

const preview: Preview = {
  parameters: {
    layout: "fullscreen",
    a11y: {
      // Email HTML intentionally violates several "web page" axe rules
      // that don't apply to a rendered email (e.g. landmarks, page
      // language on inner content) -- BaseLayout already sets <html lang>
      // and a <Preview> summary; region/landmark structure is not a
      // meaningful concept inside an email client's chrome.
      test: "error",
      config: {
        rules: [{ id: "region", enabled: false }],
      },
    },
  },
};

export default preview;
