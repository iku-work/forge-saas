import { describe, expect, it } from "vitest";

import { exampleBranding } from "./components/BaseLayout.js";
import { renderEmail } from "./render.js";
import { TEMPLATE_NAMES, TEMPLATES } from "./templates/index.js";

describe.each(TEMPLATE_NAMES)("%s", (name) => {
  it("renders html and a non-empty plain-text fallback", async () => {
    const result = await renderEmail(name, {
      branding: exampleBranding,
      props: TEMPLATES[name].exampleProps,
    });

    expect(result.html).toContain("<html");
    expect(result.text.trim().length).toBeGreaterThan(0);
  });

  it("uses table-based layout with inline styles, not flex/grid (email-client compatibility)", async () => {
    const result = await renderEmail(name, {
      branding: exampleBranding,
      props: TEMPLATES[name].exampleProps,
    });

    expect(result.html).not.toMatch(/display:\s*flex/i);
    expect(result.html).not.toMatch(/display:\s*grid/i);
  });

  it("renders the brand name and the requested locale's strings", async () => {
    const result = await renderEmail(name, {
      branding: exampleBranding,
      props: TEMPLATES[name].exampleProps,
    });

    expect(result.html).toContain(exampleBranding.app_name);
  });
});

describe("renderEmail", () => {
  it("rejects an unknown template name", async () => {
    await expect(
      renderEmail("not_a_real_template", { branding: exampleBranding }),
    ).rejects.toThrow(/unknown template/);
  });

  it("defaults to the default locale when none is given", async () => {
    const result = await renderEmail("welcome", {
      branding: exampleBranding,
      props: { app_url: "https://app.example.com/dashboard" },
    });

    expect(result.html).toContain("Welcome to ForgeSaaS");
  });

  it("renders Finnish strings when locale=fi is requested", async () => {
    const result = await renderEmail("invitation", {
      branding: exampleBranding,
      locale: "fi",
      props: { org_name: "Acme", accept_url: "https://app.example.com/invitations/tok" },
    });

    expect(result.html).toContain("Sinut on kutsuttu");
  });

  it("escapes a malicious interpolated value instead of rendering it as markup", async () => {
    const result = await renderEmail("invitation", {
      branding: exampleBranding,
      props: {
        inviter_name: "<script>alert(1)</script>",
        org_name: "Acme",
        accept_url: "https://app.example.com/invitations/tok",
      },
    });

    expect(result.html).not.toContain("<script>alert(1)</script>");
    expect(result.html).toContain("&lt;script&gt;");
  });

  it("picks up the brand's primary_color on the call-to-action button", async () => {
    const result = await renderEmail("welcome", {
      branding: { app_name: "Acme SaaS", primary_color: "#123456" },
      props: { app_url: "https://app.example.com/dashboard" },
    });

    expect(result.html).toContain("#123456");
    expect(result.html).toContain("Acme SaaS");
  });
});
