/**
 * The product, in the landing page's hero (visual overhaul, ticket 13).
 *
 * The real `@forge/ui` shell and checklist with sample content -- the same
 * components apps/web renders -- so the picture cannot drift from the app.
 * It is rendered with no `client:*` directive: Astro outputs it as static
 * HTML and ships none of React (tests/build.test.ts holds the landing page to
 * zero script bytes). Because it is never hydrated it may take a `t`
 * function, which an island could not: island props are serialised.
 *
 * The page wraps it in an `aria-hidden`, `inert` frame. It is a picture of
 * the product, not a second navigation.
 */
import { BarChart3, Folder, LayoutDashboard, Settings } from "lucide-react";

import {
  AppShell,
  Button,
  Checklist,
  ChecklistStep,
  Page,
  PageHeader,
  ShellLabel,
  ShellNavItem,
  ShellPanel,
  ShellSearchButton,
  ShellSidebar,
} from "@forge/ui";

import type { Translate } from "../lib/i18n";

const NAV = [
  { key: "nav_dashboard", icon: LayoutDashboard, active: true },
  { key: "nav_files", icon: Folder, active: false },
  { key: "nav_reports", icon: BarChart3, active: false },
  { key: "nav_settings", icon: Settings, active: false },
] as const;

const STEPS = [
  { key: "profile", done: true },
  { key: "files", done: false },
  { key: "invite", done: false },
  { key: "plan", done: false },
] as const;

export default function HeroProduct({ t }: { t: Translate }) {
  const k = (key: string): string => t(`marketing.hero_product.${key}`);
  const org = k("org");
  const firstOpen = STEPS.find((step) => !step.done);

  const header = (
    <>
      <ShellNavItem asChild className="min-h-10 font-semibold text-foreground">
        <span>
          <span className="grid size-[1.375rem] shrink-0 place-items-center rounded-sm bg-primary text-xs font-bold text-primary-foreground">
            {org.charAt(0).toUpperCase()}
          </span>
          <ShellLabel>{org}</ShellLabel>
        </span>
      </ShellNavItem>
      <ShellSearchButton label={k("search")} shortcut="⌘K" tabIndex={-1} />
    </>
  );

  return (
    <AppShell
      sidebar={
        <ShellSidebar header={header}>
          <div className="flex flex-col gap-0.5">
            {NAV.map(({ key, icon: Icon, active }) => (
              <ShellNavItem key={key} asChild>
                <span aria-current={active ? "page" : undefined}>
                  <Icon aria-hidden="true" />
                  <ShellLabel>{k(key)}</ShellLabel>
                </span>
              </ShellNavItem>
            ))}
          </div>
        </ShellSidebar>
      }
    >
      <ShellPanel top={<p className="flex-1 truncate">{k("nav_dashboard")}</p>}>
        <Page>
          <PageHeader
            title={t("marketing.hero_product.title", { org })}
            description={k("description")}
            titleTestId="hero-product-title"
          />
          <Checklist
            title={k("checklist_title")}
            done={1}
            total={STEPS.length}
            progressLabel={k("progress")}
          >
            {STEPS.map((step) => (
              <ChecklistStep
                key={step.key}
                done={step.done}
                title={k(`step_${step.key}_title`)}
                hint={k(`step_${step.key}_hint`)}
                doneLabel={k("step_done")}
                action={
                  <Button
                    tabIndex={-1}
                    variant={step === firstOpen ? "default" : "outline"}
                  >
                    {k(`step_${step.key}_action`)}
                  </Button>
                }
              />
            ))}
          </Checklist>
        </Page>
      </ShellPanel>
    </AppShell>
  );
}
