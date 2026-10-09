import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  ScrollText,
  ShieldAlert,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@forge/hooks";
import {
  AppShell,
  ShellLabel,
  ShellNavItem,
  ShellPanel,
  ShellSidebar,
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
  Toaster,
} from "@forge/ui";

import { GlobalBanner } from "../components/GlobalBanner";
import { MAIN_CONTENT_ID, SkipLink } from "../components/SkipLink";
import { useRouteLocale } from "../lib/useRouteLocale";

interface AdminNavItem {
  to:
    | "/$locale/admin"
    | "/$locale/admin/orgs"
    | "/$locale/admin/users"
    | "/$locale/admin/audit"
    | "/$locale/admin/settings";
  labelKey: string;
  icon: typeof LayoutDashboard;
}

const ADMIN_NAV: AdminNavItem[] = [
  {
    to: "/$locale/admin",
    labelKey: "common.admin.nav.dashboard",
    icon: LayoutDashboard,
  },
  {
    to: "/$locale/admin/orgs",
    labelKey: "common.admin.nav.orgs",
    icon: ListChecks,
  },
  {
    to: "/$locale/admin/users",
    labelKey: "common.admin.nav.users",
    icon: Users,
  },
  {
    to: "/$locale/admin/audit",
    labelKey: "common.admin.nav.audit",
    icon: ScrollText,
  },
  // T-263: last in the nav on purpose -- it is the only entry here that
  // *changes* the running product rather than reporting on it.
  {
    to: "/$locale/admin/settings",
    labelKey: "common.admin.nav.settings",
    icon: SlidersHorizontal,
  },
];

function AdminSidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const locale = useRouteLocale();

  const handleLogout = async (): Promise<void> => {
    await logout();
    await navigate({ to: "/$locale/login", params: { locale } });
  };

  const header = (
    <div className="flex flex-col items-start gap-2 px-2.5 pb-2 pt-1.5">
      <div className="flex items-center gap-2.5 text-sm font-semibold">
        <span
          aria-hidden="true"
          className="grid size-[1.375rem] shrink-0 place-items-center rounded-sm bg-primary text-xs font-bold text-primary-foreground"
        >
          {t("common.app.name").trim().charAt(0).toUpperCase()}
        </span>
        {t("common.admin.console")}
      </div>
      {/* T-143 / ticket 12: support staff must never mistake this surface
          for the ordinary app while acting on someone else's data. A word
          and an icon, in the warning colours -- never the accent, and never
          colour alone. */}
      <span
        data-testid="admin-marker"
        className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-2 py-0.5 text-xs font-semibold text-warning"
      >
        <ShieldAlert className="size-3.5" aria-hidden="true" />
        {t("common.admin.badge")}
      </span>
    </div>
  );

  const footer = (
    <>
      <ShellNavItem asChild>
        <Link to="/$locale" params={{ locale }} data-testid="admin-back-to-app">
          <ArrowLeft aria-hidden="true" />
          <ShellLabel>{t("common.nav.back_to_app")}</ShellLabel>
        </Link>
      </ShellNavItem>
      {user ? (
        <>
          <p className="truncate px-2.5 pt-2 text-xs text-muted-foreground">
            {user.email}
          </p>
          <ShellNavItem
            type="button"
            onClick={() => {
              void handleLogout();
            }}
          >
            <LogOut aria-hidden="true" />
            <ShellLabel>{t("common.nav.logout")}</ShellLabel>
          </ShellNavItem>
        </>
      ) : null}
    </>
  );

  return (
    <ShellSidebar header={header} footer={footer}>
      <nav className="flex flex-col gap-0.5" onClick={onNavigate}>
        {ADMIN_NAV.map((item) => {
          const Icon = item.icon;
          return (
            <ShellNavItem key={item.to} asChild>
              <Link
                to={item.to}
                params={{ locale }}
                activeOptions={{ exact: item.to === "/$locale/admin" }}
              >
                <Icon aria-hidden="true" />
                <ShellLabel>{t(item.labelKey)}</ShellLabel>
              </Link>
            </ShellNavItem>
          );
        })}
      </nav>
    </ShellSidebar>
  );
}

/**
 * The superadmin console in the same sidebar-and-panel shell as the app
 * (visual overhaul, ticket 12), told apart by the Admin marker at the top of
 * the sidebar rather than by a second interface to learn.
 */
export function AdminLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <SkipLink />
      {/* T-263: the operator sees the banner their users see -- a banner that
          is only visible outside the admin surface is one nobody notices they
          left up. */}
      <GlobalBanner />
      <AppShell sidebar={<AdminSidebar />}>
        <ShellPanel
          top={
            <>
              <Sheet open={navOpen} onOpenChange={setNavOpen}>
                <SheetTrigger
                  className="-ms-2 rounded-md p-2 text-muted-foreground hover:bg-surface-sunken hover:text-foreground @3xl/shell:hidden"
                  aria-label={t("common.nav.open_menu")}
                >
                  <Menu className="size-5" aria-hidden="true" />
                </SheetTrigger>
                <SheetContent
                  side="start"
                  className="p-0"
                  aria-describedby={undefined}
                >
                  <SheetTitle className="sr-only">
                    {t("common.nav.menu")}
                  </SheetTitle>
                  <AdminSidebar
                    onNavigate={() => {
                      setNavOpen(false);
                    }}
                  />
                </SheetContent>
              </Sheet>
              <p className="flex-1 truncate">{t("common.admin.console")}</p>
            </>
          }
        >
          <main
            id={MAIN_CONTENT_ID}
            tabIndex={-1}
            className="flex-1 focus:outline-none"
          >
            {children}
          </main>
        </ShellPanel>
      </AppShell>
      <Toaster />
    </div>
  );
}
