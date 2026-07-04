import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { LayoutDashboard, ListChecks, LogOut, ScrollText, Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@forge/hooks";
import { Badge, Toaster } from "@forge/ui";

interface AdminNavItem {
  to: string;
  labelKey: string;
  icon: typeof LayoutDashboard;
}

const ADMIN_NAV: AdminNavItem[] = [
  { to: "/admin", labelKey: "common.admin.nav.dashboard", icon: LayoutDashboard },
  { to: "/admin/orgs", labelKey: "common.admin.nav.orgs", icon: ListChecks },
  { to: "/admin/users", labelKey: "common.admin.nav.users", icon: Users },
  { to: "/admin/audit", labelKey: "common.admin.nav.audit", icon: ScrollText },
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

  const handleLogout = async (): Promise<void> => {
    await logout();
    await navigate({ to: "/login" });
  };

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <header className="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-6 py-3">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold">{t("common.app.name")}</span>
          <Badge variant="destructive">{t("common.admin.badge")}</Badge>
        </div>
        <nav className="flex items-center gap-1">
          {ADMIN_NAV.map((item) => {
            const Icon = item.icon;
            const isActive =
              item.to === "/admin"
                ? location.pathname === "/admin"
                : location.pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
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
        <div className="flex items-center gap-4">
          <Link
            to="/"
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
      <main className="flex-1 overflow-auto bg-background text-foreground">
        {children}
      </main>
      <Toaster />
    </div>
  );
}
