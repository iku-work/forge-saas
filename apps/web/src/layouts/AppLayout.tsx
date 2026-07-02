import { useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import {
  BarChart3,
  Bell,
  Building2,
  Check,
  ChevronsUpDown,
  CreditCard,
  FolderOpen,
  LayoutDashboard,
  LogOut,
  Plus,
  Shield,
  User as UserIcon,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  useAuth,
  useFeatureFlag,
  useModules,
  useOrg,
  usePermission,
} from "@forge/hooks";
import type { ActiveModules } from "@forge/hooks";
import { Permission } from "@forge/types";
import {
  Avatar,
  AvatarFallback,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Toaster,
} from "@forge/ui";

import { CreateOrgForm } from "../features/settings/CreateOrgForm";
import { NotificationBell } from "../features/notifications/NotificationBell";

interface NavItem {
  to: string;
  labelKey: string;
  icon: typeof LayoutDashboard;
  permission?: Permission;
  module?: keyof ActiveModules;
  // Org-plan-driven gate (T-121), orthogonal to `module` (product-config-driven)
  // -- both a disabled module and a plan without this flag hide the item.
  flag?: string;
}

const PRIMARY_NAV: NavItem[] = [
  { to: "/", labelKey: "common.nav.dashboard", icon: LayoutDashboard },
  {
    to: "/files",
    labelKey: "common.nav.files",
    icon: FolderOpen,
    module: "fileStorage",
  },
  {
    to: "/reports",
    labelKey: "common.nav.reports",
    icon: BarChart3,
    module: "featureFlags",
    flag: "advanced_reports",
  },
];

const SETTINGS_NAV: NavItem[] = [
  { to: "/settings/profile", labelKey: "common.nav.profile", icon: UserIcon },
  { to: "/settings/security", labelKey: "common.nav.security", icon: Shield },
  { to: "/settings/org", labelKey: "common.nav.organization", icon: Building2 },
  { to: "/settings/members", labelKey: "common.nav.members", icon: Users },
  {
    to: "/settings/billing",
    labelKey: "common.nav.billing",
    icon: CreditCard,
    permission: Permission.BILLING_READ,
    module: "billing",
  },
  {
    to: "/settings/notifications",
    labelKey: "common.nav.notifications",
    icon: Bell,
    module: "notifications",
  },
];

// Route paths that sit one level under "Settings" in the header breadcrumb
// -- "/" gets its nav label directly with no prefix (see pageTitleFor).
const SETTINGS_PAGE_TITLE_KEYS: Record<string, string> = {
  "/settings/profile": "common.nav.profile",
  "/settings/security": "common.nav.security",
  "/settings/org": "common.nav.organization",
  "/settings/members": "common.nav.members",
  "/settings/billing": "common.nav.billing",
  "/settings/notifications": "common.nav.notifications",
};

function pageTitleFor(pathname: string, t: (key: string) => string): string {
  if (pathname === "/files") return t("common.nav.files");
  if (pathname === "/reports") return t("common.nav.reports");
  const settingsKey = SETTINGS_PAGE_TITLE_KEYS[pathname];
  if (settingsKey) {
    return `${t("common.nav.settings")} / ${t(settingsKey)}`;
  }
  return t("common.nav.dashboard");
}

function initialsFor(email: string): string {
  return email.slice(0, 2).toUpperCase();
}

function NavLink({ item }: { item: NavItem }) {
  const { t } = useTranslation();
  // usePermission must run unconditionally (hooks can't be called
  // conditionally) -- MEMBERS_READ is just a harmless placeholder for items
  // with no `permission`, since the result below is only consulted when one
  // was actually declared.
  const hasPermission = usePermission(
    item.permission ?? Permission.MEMBERS_READ,
  );
  // Same unconditional-call-with-a-placeholder reasoning as usePermission
  // above -- item.flag is only consulted below when one was actually declared.
  const hasFlag = useFeatureFlag(item.flag ?? "");
  if (item.permission && !hasPermission) {
    return null;
  }
  if (item.flag && !hasFlag) {
    return null;
  }
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      activeProps={{
        className: "bg-accent text-accent-foreground font-medium",
      }}
      activeOptions={{ exact: item.to === "/" }}
    >
      <Icon className="size-4" aria-hidden="true" />
      {t(item.labelKey)}
    </Link>
  );
}

function OrgSwitcher() {
  const { t } = useTranslation();
  const { orgs, org, isLoading, switchOrg } = useOrg();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm font-medium"
            disabled={isLoading}
          >
            <span className="truncate">
              {isLoading
                ? t("common.org_switcher.loading")
                : (org?.name ?? t("common.org_switcher.select"))}
            </span>
            <ChevronsUpDown
              className="size-4 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuLabel>
            {t("common.org_switcher.label")}
          </DropdownMenuLabel>
          {orgs.map((candidate) => (
            <DropdownMenuItem
              key={candidate.id}
              onSelect={() => {
                switchOrg(candidate.id);
              }}
            >
              <span className="flex-1 truncate">{candidate.name}</span>
              {candidate.id === org?.id ? (
                <Check className="size-4" aria-hidden="true" />
              ) : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              setCreateOpen(true);
            }}
          >
            <Plus className="size-4" aria-hidden="true" />
            {t("common.org_switcher.create")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("common.org_switcher.create")}</DialogTitle>
          </DialogHeader>
          <CreateOrgForm
            onCreated={() => {
              setCreateOpen(false);
            }}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

function UserMenu() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  if (!user) {
    return null;
  }

  const handleLogout = async (): Promise<void> => {
    await logout();
    await navigate({ to: "/login" });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent"
        >
          <Avatar className="size-7">
            <AvatarFallback>{initialsFor(user.email)}</AvatarFallback>
          </Avatar>
          <span className="flex-1 truncate">
            {user.full_name ?? user.email}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel className="truncate">{user.email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            void handleLogout();
          }}
        >
          <LogOut className="size-4" aria-hidden="true" />
          {t("common.nav.logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function CreateFirstOrgPrompt() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          {/* CardTitle renders a <div>, not a heading -- this page needs a
              real <h1> for a11y and the document outline (same pattern as
              features/auth/AuthCard.tsx). */}
          <h1 className="text-xl font-semibold leading-none tracking-tight">
            {t("tenancy.org_create.title")}
          </h1>
          <CardDescription>
            {t("tenancy.org_create.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <CreateOrgForm />
        </CardContent>
      </Card>
    </div>
  );
}

export function AppLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { orgs, isLoading } = useOrg();
  const modules = useModules();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <span className="text-sm text-muted-foreground">
          {t("common.status.loading")}
        </span>
      </div>
    );
  }

  if (orgs.length === 0) {
    return <CreateFirstOrgPrompt />;
  }

  const pageTitle = pageTitleFor(location.pathname, t);

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-64 shrink-0 flex-col border-r p-4">
        <div className="px-2 pb-4 text-lg font-semibold">
          {t("common.app.name")}
        </div>
        {modules.multiOrg ? (
          <div className="pb-4">
            <OrgSwitcher />
          </div>
        ) : null}
        <nav className="flex flex-1 flex-col gap-1">
          {PRIMARY_NAV.filter(
            (item) => !item.module || modules[item.module],
          ).map((item) => (
            <NavLink key={item.to} item={item} />
          ))}
          <div className="mt-4 px-3 text-xs font-medium uppercase text-muted-foreground">
            {t("common.nav.settings")}
          </div>
          {SETTINGS_NAV.filter(
            (item) => !item.module || modules[item.module],
          ).map((item) => (
            <NavLink key={item.to} item={item} />
          ))}
        </nav>
        <div className="border-t pt-2">
          <UserMenu />
        </div>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b px-6 py-3">
          {/* Not a heading -- the breadcrumb duplicates the page's own <h1>
              (e.g. DashboardPage's "Dashboard"); a second <h1> with the same
              text would be both invalid document structure and ambiguous
              for screen-reader/role-based queries. */}
          <p className="text-sm font-medium text-muted-foreground">
            {pageTitle}
          </p>
          {modules.notifications ? (
            <NotificationBell />
          ) : (
            <button
              type="button"
              disabled
              aria-label={t("notifications.coming_soon")}
              className="rounded-md p-2 text-muted-foreground opacity-50"
            >
              <Bell className="size-4" aria-hidden="true" />
            </button>
          )}
        </header>
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
      <Toaster />
    </div>
  );
}
