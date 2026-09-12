import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import {
  LayoutDashboard,
  ListChecks,
  LogOut,
  ScrollText,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@forge/hooks";
import { Badge, Toaster } from "@forge/ui";

import { GlobalBanner } from "../components/GlobalBanner";
import { stripLocale } from "../lib/locale-url";
import { useRouteLocale } from "../lib/useRouteLocale";

interface AdminNavItem {
  // T-233: the router path (locale-prefixed) and the locale-less path the
  // active-state check compares against are deliberately separate fields --
  // deriving the second by string-surgery on the first is the kind of thing
  // that silently stops matching when a route is renamed.
  to:
    | "/$locale/admin"
    | "/$locale/admin/orgs"
    | "/$locale/admin/users"
    | "/$locale/admin/audit"
    | "/$locale/admin/settings";
  path: string;
  labelKey: string;
  icon: typeof LayoutDashboard;
}

const ADMIN_NAV: AdminNavItem[] = [
  {
    to: "/$locale/admin",
    path: "/admin",
    labelKey: "common.admin.nav.dashboard",
    icon: LayoutDashboard,
  },
  {
    to: "/$locale/admin/orgs",
    path: "/admin/orgs",
    labelKey: "common.admin.nav.orgs",
    icon: ListChecks,
  },
  {
    to: "/$locale/admin/users",
    path: "/admin/users",
    labelKey: "common.admin.nav.users",
    icon: Users,
  },
  {
    to: "/$locale/admin/audit",
    path: "/admin/audit",
    labelKey: "common.admin.nav.audit",
    icon: ScrollText,
  },
  // T-263: last in the nav on purpose -- it is the only entry here that
  // *changes* the running product rather than reporting on it.
  {
    to: "/$locale/admin/settings",
    path: "/admin/settings",
    labelKey: "common.admin.nav.settings",
    icon: SlidersHorizontal,
  },
];

// T-143: a deliberately distinct shell (dark header, red "Admin" badge, no
// org switcher) from AppLayout.tsx -- support staff must never mistake this
// surface for the ordinary user-facing app while acting on someone else's
// data.
export function AdminLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const locale = useRouteLocale();
  const activePath = stripLocale(location.pathname);

  const handleLogout = async (): Promise<void> => {
    await logout();
    await navigate({ to: "/$locale/login", params: { locale } });
  };

  return (
    <div className="flex min-h-dvh flex-col bg-slate-950 text-slate-100">
      {/* Wraps, because this bar holds a wordmark, four nav links and an
          account block -- 901px of content that used to sit in one
          unwrappable row, so every admin page scrolled sideways on a phone
          (measured: 901px document width in a 360px viewport). */}
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-800 bg-slate-900 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold">{t("common.app.name")}</span>
          <Badge variant="destructive">{t("common.admin.badge")}</Badge>
        </div>
        <nav className="flex flex-wrap items-center gap-1">
          {ADMIN_NAV.map((item) => {
            const Icon = item.icon;
            const isActive =
              item.path === "/admin"
                ? activePath === "/admin"
                : activePath.startsWith(item.path);
            return (
              <Link
                key={item.to}
                to={item.to}
                params={{ locale }}
                className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors hover:bg-slate-800 ${
                  isActive ? "bg-slate-800 text-white" : "text-slate-300"
                }`}
              >
                <Icon className="size-4" aria-hidden="true" />
                {t(item.labelKey)}
              </Link>
            );
          })}
        </nav>
        <div className="flex min-w-0 items-center gap-4">
          <Link
            to="/$locale"
            params={{ locale }}
            className="text-sm text-slate-300 underline underline-offset-4 hover:text-white"
          >
            {t("common.admin.back_to_app")}
          </Link>
          {user ? (
            <div className="flex items-center gap-2 text-sm text-slate-300">
              <span className="truncate">{user.email}</span>
              <button
                type="button"
                onClick={() => {
                  void handleLogout();
                }}
                aria-label={t("common.nav.logout")}
                className="rounded-md p-1.5 hover:bg-slate-800 hover:text-white"
              >
                <LogOut className="size-4" aria-hidden="true" />
              </button>
            </div>
          ) : null}
        </div>
      </header>
      {/* T-263: the operator sees the banner their users see -- a banner that
          is only visible outside the admin surface is one nobody notices they
          left up. */}
      <GlobalBanner />
      <main className="flex-1 overflow-auto bg-background text-foreground">
        {children}
      </main>
      <Toaster />
    </div>
  );
}
