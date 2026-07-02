// @react-email/components and the individual @react-email/* primitive
// packages were deprecated upstream in React Email 6.0 (April 2026) --
// "Package no longer supported" on npm for every published version. All
// components and the render() utility now ship from the single `react-email`
// package instead (see https://react.email/docs/getting-started/updating-react-email).
import { Body, Button, Container, Head, Hr, Html, Preview, Section, Text } from "react-email";
import type { ReactNode } from "react";

// Field names mirror app.core.module_loader.Branding (backend/app/core/module_loader.py)
// verbatim -- the Python `send_email` job passes get_branding() straight through as JSON,
// with no camelCase translation layer in between.
export interface Branding {
  app_name: string;
  primary_color: string;
}

export const exampleBranding: Branding = {
  app_name: "ForgeSaaS",
  primary_color: "#7c6ff7",
};

interface BaseLayoutProps {
  previewText: string;
  branding: Branding;
  footerText: string;
  children: ReactNode;
}

export function BaseLayout({ previewText, branding, footerText, children }: BaseLayoutProps) {
  return (
    <Html lang="en">
      <Head />
      <Preview>{previewText}</Preview>
      <Body
        style={{
          backgroundColor: "#f4f5f7",
          fontFamily: "Helvetica,Arial,sans-serif",
          margin: 0,
          padding: "40px 0",
        }}
      >
        <Container
          style={{ backgroundColor: "#ffffff", borderRadius: 8, padding: 32, maxWidth: 480 }}
        >
          <Text style={{ fontSize: 20, fontWeight: "bold", color: "#111827", margin: "0 0 24px" }}>
            {branding.app_name}
          </Text>
          <Section style={{ fontSize: 15, lineHeight: 1.5, color: "#374151" }}>{children}</Section>
          <Hr style={{ borderColor: "#e5e7eb", margin: "32px 0 16px" }} />
          <Text style={{ fontSize: 12, color: "#9ca3af", margin: 0 }}>{footerText}</Text>
        </Container>
      </Body>
    </Html>
  );
}

interface CtaButtonProps {
  href: string;
  branding: Branding;
  children: ReactNode;
}

export function CtaButton({ href, branding, children }: CtaButtonProps) {
  return (
    <Section style={{ textAlign: "center", margin: "32px 0" }}>
      <Button
        href={href}
        style={{
          backgroundColor: branding.primary_color,
          color: "#ffffff",
          textDecoration: "none",
          padding: "12px 24px",
          borderRadius: 6,
          fontWeight: "bold",
          display: "inline-block",
        }}
      >
        {children}
      </Button>
    </Section>
  );
}
