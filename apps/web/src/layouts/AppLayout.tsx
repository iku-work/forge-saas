import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import {
  Bell,
  Check,
  Search,
  ChevronsUpDown,
  LogOut,
  Plus,
  ShieldAlert,
  Menu,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth, useMaintenance, useModules, useOrg } from "@forge/hooks";
import type { ActiveModules } from "@forge/hooks";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
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
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@forge/ui";

import { GlobalBanner } from "../components/GlobalBanner";
import { CommandPalette } from "../features/command-palette/CommandPalette";
import { recordRecentPath } from "../features/command-palette/recent";
import { isAppleDevice, renderKey } from "../features/shortcuts/registry";
import { ShortcutsDialog } from "../features/shortcuts/ShortcutsDialog";
import { useGlobalShortcuts } from "../features/shortcuts/useGlobalShortcuts";
import { MAIN_CONTENT_ID, SkipLink } from "../components/SkipLink";
import { MaintenanceScreen } from "../components/MaintenanceScreen";
import { CreateOrgForm } from "../features/settings/CreateOrgForm";
import { NotificationBell } from "../features/notifications/NotificationBell";
import { SupportLink } from "../features/support/SupportLink";
import { stripLocale } from "../lib/locale-url";
import { useVisibleNavItems, type NavItem } from "../lib/navigation";
import { useAvatarSrc } from "../lib/useAvatarSrc";
import { useRouteLocale } from "../lib/useRouteLocale";

// Route paths that sit one level under "Settings" in the header breadcrumb
// -- "/" gets its nav label directly with no prefix (see pageTitleFor).
const SETTINGS_PAGE_TITLE_KEYS: Record<string, string> = {
  "/settings/profile": "common.nav.profile",
  "/settings/security": "common.nav.security",
  "/settings/org": "common.nav.organization",
  "/settings/members": "common.nav.members",
  "/settings/billing": "common.nav.billing",
  "/settings/notifications": "common.nav.notifications",
  "/settings/api-keys": "common.nav.api_keys",
  "/settings/webhooks": "common.nav.webhooks",
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

// "/" -> "dashboard", "/settings/members" -> "settings-members"
function navTestSlug(path: string): string {
  return path === "/"
    ? "dashboard"
    : path.replace(/^\//, "").replace(/\//g, "-");
}

function NavLink({ item }: { item: NavItem }) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  // No permission/flag check here any more: useVisibleNavItems() (lib/
  // navigation.ts) applies all three gates to the whole list before it gets
  // here, so the palette and the sidebar cannot disagree about what exists.
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      params={{ locale }}
      className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      activeProps={{
        className: "bg-accent text-accent-foreground font-medium",
      }}
      activeOptions={{ exact: item.path === "/" }}
      data-testid={`nav-${navTestSlug(item.path)}`}
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
            data-testid="org-switcher-trigger"
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
            data-testid="org-switcher-create"
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
  const locale = useRouteLocale();
  // Above the early return, not beside the <Avatar> -- `if (!user) return`
  // below would otherwise make this a conditional hook (eslint
  // react-hooks/rules-of-hooks). The hook handles a null user itself.
  const avatarSrc = useAvatarSrc();

  if (!user) {
    return null;
  }

  const handleLogout = async (): Promise<void> => {
    await logout();
    await navigate({ to: "/$locale/login", params: { locale } });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-start text-sm hover:bg-accent"
        >
          <Avatar className="size-7" data-testid="user-menu-avatar">
            {avatarSrc ? <AvatarImage src={avatarSrc} alt="" /> : null}
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
        {user.is_superadmin ? (
          <>
            <DropdownMenuItem asChild>
              <Link to="/$locale/admin" params={{ locale }}>
                <ShieldAlert className="size-4" aria-hidden="true" />
                {t("common.admin.user_menu_link")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        ) : null}
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
    <div
      className="flex min-h-dvh items-center justify-center p-4"
      data-testid="create-first-org-prompt"
    >
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

function SidebarNav({
  modules,
  onNavigate,
}: {
  modules: ActiveModules;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const nav = useVisibleNavItems();

  return (
    <>
      <div className="px-2 pb-4 text-lg font-semibold">
        {t("common.app.name")}
      </div>
      {modules.multiOrg ? (
        <div className="pb-4">
          <OrgSwitcher />
        </div>
      ) : null}
      {/* onClick rather than a route subscription: tapping a link in the
          drawer has to close it, and the drawer does not unmount on
          navigation because the shell that owns it never unmounts. */}
      <nav className="flex flex-1 flex-col gap-1" onClick={onNavigate}>
        {nav.primary.map((item) => (
          <NavLink key={item.to} item={item} />
        ))}
        <div className="mt-4 px-3 text-caption font-medium uppercase text-muted-foreground">
          {t("common.nav.settings")}
        </div>
        {nav.settings.map((item) => (
          <NavLink key={item.to} item={item} />
        ))}
      </nav>
      <div className="border-t pt-2">
        <SupportLink />
        <UserMenu />
      </div>
    </>
  );
}

export function AppLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { orgs, isLoading } = useOrg();
  const modules = useModules();
  const maintenance = useMaintenance();
  const location = useLocation();
  // Above the early returns below: this component returns before its main
  // body for maintenance, loading and no-org, and a hook after one of those
  // is a different hook order on a later render.
  const [navOpen, setNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const nav = useVisibleNavItems();
  const navigate = useNavigate();
  const locale = useRouteLocale();
  const activePath = stripLocale(location.pathname);

  // F-005. Recorded from the shell rather than from each page: this is the
  // one place that sees every in-app navigation, and a page-level call would
  // have to be remembered for every page added afterwards. Only paths that
  // are actually nav destinations are stored -- the palette resolves them
  // back to nav items, so anything else would be dead weight.
  useEffect(() => {
    if (nav.all.some((item) => item.path === activePath)) {
      recordRecentPath(activePath);
    }
  }, [activePath, nav]);

  useGlobalShortcuts({
    onCommandPalette: () => {
      setPaletteOpen((wasOpen) => !wasOpen);
    },
    onShowShortcuts: () => {
      setShortcutsOpen(true);
    },
    onNavigate: (path) => {
      // Resolved against the visible nav, so `g f` does nothing at all when
      // file storage is off rather than navigating to a route that will
      // bounce the user back.
      const target = nav.all.find((item) => item.path === path);
      if (target) {
        void navigate({ to: target.to, params: { locale } });
      }
    },
  });

  // T-263, checked before the loading state: during a maintenance window every
  // request this shell would make answers 503, so rendering it at all produces
  // a wall of error toasts on a half-populated page. The admin shell
  // (AdminLayout) is a separate route tree and deliberately does *not* do this
  // -- it is the surface that ends the window.
  if (maintenance.enabled) {
    return <MaintenanceScreen maintenance={maintenance} />;
  }

  if (isLoading) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <span className="text-sm text-muted-foreground">
          {t("common.status.loading")}
        </span>
      </div>
    );
  }

  if (orgs.length === 0) {
    return <CreateFirstOrgPrompt />;
  }

  const pageTitle = pageTitleFor(activePath, t);

  return (
    // T-263: the banner sits *outside* the shell's flex row, above everything,
    // so it pushes the whole app down rather than landing inside one column.
    <div className="flex min-h-dvh flex-col" data-testid="app-shell">
      {/* First child, before the banner: the skip link only does its job if
          it is the first thing Tab reaches (F-013). */}
      <SkipLink />
      <GlobalBanner />
      <div className="flex flex-1">
        {/* One nav, two presentations. Below `md` it is a drawer behind the
            header's menu button; from `md` up it is the column it always
            was. Rendering the same <SidebarNav> in both is what stops the
            phone getting a nav that quietly drifts from the desktop one. */}
        <aside className="hidden w-64 shrink-0 flex-col border-e p-4 md:flex">
          <SidebarNav modules={modules} />
        </aside>

        {/* min-w-0 is load-bearing, not tidying. A flex item defaults to
            `min-width: auto`, which means it refuses to shrink below its
            content's intrinsic minimum -- so one wide descendant (the
            members list) pushed this column to 408px inside a 360px phone
            and every page it hosts scrolled sideways. Measured: setting
            min-width:0 here took the document's scrollWidth from 408 back
            to 360. It has to be on this column rather than on the offending
            page, because the column is what the page is stretched by. */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b px-4 py-3 sm:px-6">
            <Sheet open={navOpen} onOpenChange={setNavOpen}>
              <SheetTrigger
                data-testid="nav-drawer-trigger"
                className="-ms-1 rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground md:hidden"
                aria-label={t("common.nav.open_menu")}
              >
                <Menu className="size-5" aria-hidden="true" />
              </SheetTrigger>
              <SheetContent side="start" aria-describedby={undefined}>
                {/* Radix requires a title on every dialog for the accessible
                    name. The drawer shows its own wordmark, so this one is
                    visually hidden rather than duplicated on screen. */}
                <SheetTitle className="sr-only">
                  {t("common.nav.menu")}
                </SheetTitle>
                <SidebarNav
                  modules={modules}
                  onNavigate={() => {
                    setNavOpen(false);
                  }}
                />
              </SheetContent>
            </Sheet>
            {/* Not a heading -- the breadcrumb duplicates the page's own <h1>
                (e.g. DashboardPage's "Dashboard"); a second <h1> with the same
                text would be both invalid document structure and ambiguous
                for screen-reader/role-based queries. */}
            <p className="flex-1 truncate text-body-sm font-medium text-muted-foreground">
              {pageTitle}
            </p>
            {/* F-005 discoverability: a palette nobody knows about is a
                palette nobody uses, so the hotkey is shown rather than
                described. Also a real button -- ⌘K is unreachable on a
                touch device, and this is the only way in there. */}
            <button
              type="button"
              onClick={() => {
                setPaletteOpen(true);
              }}
              data-testid="palette-trigger"
              aria-keyshortcuts="Meta+K Control+K"
              className="flex min-h-11 items-center gap-2 rounded-md border px-2 text-caption text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground sm:px-3"
            >
              <Search className="size-4 shrink-0" aria-hidden="true" />
              <span className="hidden sm:inline">
                {t("common.palette.trigger")}
              </span>
              {/* aria-hidden: the shortcut is already announced through
                  aria-keyshortcuts above, and "⌘ K" read out as content is
                  noise. */}
              <kbd
                aria-hidden="true"
                className="hidden rounded border bg-muted px-1.5 py-0.5 sm:inline"
              >
                {`${renderKey("mod", isAppleDevice())}K`}
              </kbd>
            </button>
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
          {/* tabIndex={-1} so the skip link actually moves focus here.
              Without it the fragment jump scrolls the viewport but leaves
              focus on <body>, and the next Tab starts from the top of the
              document again -- the exact trap the link exists to avoid. */}
          <main
            id={MAIN_CONTENT_ID}
            tabIndex={-1}
            className="flex-1 overflow-auto focus:outline-none"
          >
            {children}
          </main>
        </div>
      </div>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onShowShortcuts={() => {
          setShortcutsOpen(true);
        }}
      />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      <Toaster />
    </div>
  );
}
