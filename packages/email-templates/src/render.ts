import { DEFAULT_LOCALE } from "@forge/i18n";
import { render as renderToString } from "react-email";

import type { Branding } from "./components/BaseLayout.js";
import { isTemplateName, TEMPLATES } from "./templates/index.js";

export interface RenderedEmail {
  html: string;
  text: string;
}

export interface RenderEmailInput {
  locale?: string;
  branding: Branding;
  // `object`, not Record<string, unknown> -- callers (including this
  // package's own tests) pass a specific template's named Props interface
  // here, which (being a closed named type, not a fresh literal) doesn't
  // structurally satisfy an index-signature type. See templates/index.ts's
  // defineTemplate for the same constraint, for the same reason.
  props?: object;
}

export async function renderEmail(
  templateName: string,
  input: RenderEmailInput,
): Promise<RenderedEmail> {
  if (!isTemplateName(templateName)) {
    throw new Error(`@forge/email-templates: unknown template "${templateName}"`);
  }
  const locale = input.locale ?? DEFAULT_LOCALE;
  const mergedProps: Record<string, unknown> = {
    ...input.props,
    locale,
    branding: input.branding,
  };
  const element = TEMPLATES[templateName].render(mergedProps);
  const [html, text] = await Promise.all([
    renderToString(element, { pretty: false }),
    renderToString(element, { plainText: true }),
  ]);
  return { html, text };
}
