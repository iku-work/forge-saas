import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./index.css";
import { AppRoot } from "./AppRoot";
import { initI18n } from "./i18n";
import { createQueryClient } from "./lib/queryClient";
import { initSentry } from "./lib/sentry";
import { createAppRouter } from "./router";

initSentry();

const queryClient = createQueryClient();
const router = createAppRouter(queryClient);

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}

// Awaited before the first render so the default locale's resources (and
// whichever locale localStorage/the browser actually resolved to) are ready
// the instant anything mounts -- see apps/web/src/i18n.ts's module docstring
// for why this isn't done via React Suspense instead.
await initI18n();

createRoot(rootElement).render(
  <StrictMode>
    <AppRoot router={router} queryClient={queryClient} />
  </StrictMode>,
);
