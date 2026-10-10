import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  Bell,
  Check,
  ChevronsUpDown,
  LayoutDashboard,
  LogOut,
  Plus,
  Settings,
  ShieldAlert,
  Menu,
  PanelLeft,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  useAuth,
  useDirection,
  useMaintenance,
  useModules,
  useOrg,
} from "@forge/hooks";
import type { ActiveModules } from "@forge/hooks";
import {
  AppShell,
  Avatar,
  AvatarFallback,
  AvatarImage,
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
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
  ShellLabel,
  ShellNavGroup,
  ShellNavItem,
  ShellPanel,
  ShellSearchButton,
  ShellSidebar,
} from "@forge/ui";

import { GlobalBanner } from "../components/GlobalBanner";
import { CommandPalette } from "../features/command-palette/CommandPalette";
import { recordRecentPath } from "../features/command-palette/recent";
import { isAppleDevice, renderChord } from "../features/shortcuts/registry";
import { ShortcutsDialog } from "../features/shortcuts/ShortcutsDialog";
import { useGlobalShortcuts } from "../features/shortcuts/useGlobalShortcuts";
import { MAIN_CONTENT_ID, SkipLink } from "../components/SkipLink";
import { MaintenanceScreen } from "../components/MaintenanceScreen";
import { AuthLayout } from "../features/auth/AuthLayout";
import { CreateOrgForm } from "../features/settings/CreateOrgForm";
import { NotificationBell } from "../features/notifications/NotificationBell";
import { SupportLink } from "../features/support/SupportLink";
import { stripLocale } from "../lib/locale-url";
import { useVisibleNavItems, type NavItem } from "../lib/navigation";
import { useAvatarSrc } from "../lib/useAvatarSrc";
import { useOrgLogoSrc } from "../lib/useOrgLogoSrc";
import { useSidebarCollapsed } from "../lib/useSidebarCollapsed";
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

/**
 * In the collapsed rail a row is an icon alone, so its name appears as a
 * tooltip on hover and on keyboard focus. Expanded (and in the drawer, which
 * never collapses) the row shows its label and needs no tooltip.
 */
function RailTooltip({
  label,
  enabled,
  children,
}: {
  label: string;
  enabled: boolean;
  children: ReactNode;
}) {
  const direction = useDirection();
  if (!enabled) {
    return children;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      {/* Radix sides are physical; the rail is on the start edge. */}
      <TooltipContent side={direction === "rtl" ? "left" : "right"}>
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

function NavLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  // No permission/flag check here any more: useVisibleNavItems() (lib/
  // navigation.ts) applies all three gates to the whole list before it gets
  // here, so the palette and the sidebar cannot disagree about what exists.
  // The active style comes from the aria-current="page" the router sets on
  // the active link, so the look and what is announced cannot disagree.
  const Icon = item.icon;
  return (
    <RailTooltip label={t(item.labelKey)} enabled={collapsed}>
      <ShellNavItem asChild>
        <Link
          to={item.to}
          params={{ locale }}
          activeOptions={{ exact: item.path === "/" }}
          data-testid={`nav-${navTestSlug(item.path)}`}
        >
          <Icon aria-hidden="true" />
          <ShellLabel>{t(item.labelKey)}</ShellLabel>
        </Link>
      </ShellNavItem>
    </RailTooltip>
  );
}

/** The square initial that stands for the Organization (or the product). */
function Monogram({
  name,
  src = null,
}: {
  name: string | undefined;
  /** An uploaded logo, shown in place of the initial. */
  src?: string | null;
}) {
  if (src) {
    return (
      <img
        src={src}
        alt=""
        aria-hidden="true"
        className="size-[1.375rem] shrink-0 rounded-sm object-cover"
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      className="grid size-[1.375rem] shrink-0 place-items-center rounded-sm bg-primary text-xs font-bold text-primary-foreground"
    >
      {name?.trim().charAt(0).toUpperCase()}
    </span>
  );
}

function OrgSwitcher() {
  const { t } = useTranslation();
  const { orgs, org, isLoading, switchOrg } = useOrg();
  const logoSrc = useOrgLogoSrc(org);
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <ShellNavItem
            data-testid="org-switcher-trigger"
            className="min-h-10 font-semibold text-foreground"
            disabled={isLoading}
          >
            <Monogram name={org?.name} src={logoSrc} />
            <ShellLabel>
              {isLoading
                ? t("common.org_switcher.loading")
                : (org?.name ?? t("common.org_switcher.select"))}
            </ShellLabel>
            <ChevronsUpDown
              className="text-muted-foreground group-data-[collapsed]/shell:hidden"
              aria-hidden="true"
            />
          </ShellNavItem>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuLabel>
            {t("common.org_switcher.label")}
          </DropdownMenuLabel>
          {orgs.map((candidate) => (
            <DropdownMenuItem
              key={candidate.id}
              data-testid="org-switcher-option"
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
        {/* The account row at the foot of the sidebar. min-h-11 keeps it on
            the WCAG 2.2 target-size floor with room to spare (F-014). */}
        <ShellNavItem
          data-testid="user-menu-trigger"
          className="min-h-11 min-w-0 flex-1 text-foreground"
        >
          <Avatar className="size-7" data-testid="user-menu-avatar">
            {avatarSrc ? <AvatarImage src={avatarSrc} alt="" /> : null}
            <AvatarFallback>{initialsFor(user.email)}</AvatarFallback>
          </Avatar>
          <span className="min-w-0 flex-1 leading-tight group-data-[collapsed]/shell:sr-only">
            <span className="block truncate">
              {user.full_name ?? user.email}
            </span>
            {user.full_name ? (
              <span className="block truncate text-xs font-normal text-muted-foreground">
                {user.email}
              </span>
            ) : null}
          </span>
        </ShellNavItem>
      </DropdownMenuTrigger>
      {/* Opens upwards from the foot of the sidebar; `start` is logical, so
          it stays anchored to the sidebar's edge under rtl. */}
      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuLabel className="truncate">{user.email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {/* The settings pages left the sidebar, so this menu is now the only
            way to reach them by pointer -- and Dashboard is here as its
            counterpart, because the sidebar no longer shows the primary nav
            while you are inside settings. */}
        <DropdownMenuItem asChild>
          <Link
            to="/$locale"
            params={{ locale }}
            data-testid="user-menu-dashboard"
          >
            <LayoutDashboard className="size-4" aria-hidden="true" />
            {t("common.nav.dashboard")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link
            to="/$locale/settings/profile"
            params={{ locale }}
            data-testid="user-menu-settings"
          >
            <Settings className="size-4" aria-hidden="true" />
            {t("common.nav.settings")}
          </Link>
        </DropdownMenuItem>
        {user.is_superadmin ? (
          <DropdownMenuItem asChild>
            <Link to="/$locale/admin" params={{ locale }}>
              <ShieldAlert className="size-4" aria-hidden="true" />
              {t("common.admin.user_menu_link")}
            </Link>
          </DropdownMenuItem>
        ) : null}
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
    <div data-testid="create-first-org-prompt">
      <AuthLayout
        title={t("tenancy.org_create.title")}
        description={t("tenancy.org_create.description")}
      >
        <CreateOrgForm />
      </AuthLayout>
    </div>
  );
}

/**
 * The sidebar shows one group at a time, chosen by where you are.
 *
 * Settings used to sit under the primary nav as a second, permanently
 * expanded list, which made the sidebar eleven items long and put
 * "Notifications" and "Webhooks" at the same level as "Dashboard" -- two
 * things a user reaches for at very different rates. Hick's law is the
 * argument (§1.2: five to seven items per level), and the settings pages are
 * a mode you enter rather than destinations you keep beside your work.
 *
 * Getting back out is the cost, so there are several ways: "Back to app" at
 * the top of the settings sidebar, Dashboard in the user menu, the
 * breadcrumb, and ⌘K, which reaches every page from either mode.
 *
 * This renders the whole sidebar -- header, navigation and footer -- so the
 * desktop column and the phone drawer are the same component and cannot
 * drift apart.
 */
function SidebarNav({
  modules,
  inSettings,
  onNavigate,
  onOpenPalette,
  collapsed = false,
}: {
  modules: ActiveModules;
  inSettings: boolean;
  onNavigate?: () => void;
  onOpenPalette: () => void;
  /** Rail mode: tooltips on. The drawer copy never passes it. */
  collapsed?: boolean;
}) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const { org } = useOrg();
  const nav = useVisibleNavItems();

  const header = (
    <>
      {/* With several organizations the switcher is the top row; without,
          the product's wordmark is, and links home (Jakob's law). */}
      {modules.multiOrg ? (
        <OrgSwitcher />
      ) : (
        <ShellNavItem
          asChild
          className="min-h-10 font-semibold text-foreground"
        >
          <Link
            to="/$locale"
            params={{ locale }}
            data-testid="sidebar-wordmark"
          >
            <Monogram name={t("common.app.name")} />
            <ShellLabel>{t("common.app.name")}</ShellLabel>
          </Link>
        </ShellNavItem>
      )}
      {/* F-005 discoverability: a palette nobody knows about is a palette
          nobody uses, so the hotkey is shown rather than described. Also a
          real button -- ⌘K is unreachable on a touch device. */}
      <RailTooltip label={t("common.palette.trigger")} enabled={collapsed}>
        <ShellSearchButton
          data-testid="palette-trigger"
          aria-keyshortcuts="Meta+K Control+K"
          label={t("common.palette.trigger")}
          shortcut={renderChord(["mod", "K"], isAppleDevice())}
          onClick={onOpenPalette}
        />
      </RailTooltip>
    </>
  );

  const footer = (
    <>
      <SupportLink />
      {/* Side by side when expanded; stacked in the rail. */}
      <div className="flex items-center gap-1 group-data-[collapsed]/shell:flex-col">
        <UserMenu />
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
      </div>
    </>
  );

  const settingsGroups = (["account", "organization"] as const).map(
    (group) => ({
      group,
      label:
        group === "account"
          ? t("common.nav.group_account")
          : (org?.name ?? t("common.nav.organization")),
      items: nav.settings.filter((item) => item.group === group),
    }),
  );

  return (
    <ShellSidebar header={header} footer={footer}>
      {/* onClick rather than a route subscription: tapping a link in the
          drawer has to close it, and the drawer does not unmount on
          navigation because the shell that owns it never unmounts. */}
      <nav
        className="flex flex-col gap-0.5"
        onClick={onNavigate}
        data-testid={
          inSettings ? "sidebar-settings-nav" : "sidebar-primary-nav"
        }
      >
        {inSettings ? (
          <>
            <RailTooltip
              label={t("common.nav.back_to_app")}
              enabled={collapsed}
            >
              <ShellNavItem asChild>
                <Link
                  to="/$locale"
                  params={{ locale }}
                  data-testid="nav-back-to-app"
                >
                  <ArrowLeft aria-hidden="true" />
                  <ShellLabel>{t("common.nav.back_to_app")}</ShellLabel>
                </Link>
              </ShellNavItem>
            </RailTooltip>
            {settingsGroups.map(({ group, label, items }) =>
              items.length > 0 ? (
                <ShellNavGroup key={group} label={label}>
                  {items.map((item) => (
                    <NavLink key={item.to} item={item} collapsed={collapsed} />
                  ))}
                </ShellNavGroup>
              ) : null,
            )}
          </>
        ) : (
          nav.primary.map((item) => (
            <NavLink key={item.to} item={item} collapsed={collapsed} />
          ))
        )}
      </nav>
    </ShellSidebar>
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
  // Which group the sidebar shows. Prefix match rather than an exact list, so
  // a settings page added later is in the right mode without being registered
  // anywhere a second time.
  const inSettings = activePath.startsWith("/settings");

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

  const sidebar = useSidebarCollapsed();

  useGlobalShortcuts({
    onToggleSidebar: sidebar.toggle,
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

  const toggleLabel = t(
    sidebar.collapsed
      ? "common.nav.expand_sidebar"
      : "common.nav.collapse_sidebar",
  );

  const openPalette = (): void => {
    setNavOpen(false);
    setPaletteOpen(true);
  };

  return (
    // T-263: the banner sits *outside* the shell, above everything, so it
    // pushes the whole app down rather than landing inside one column.
    <div
      className="flex min-h-dvh flex-col bg-background"
      data-testid="app-shell"
    >
      {/* First child, before the banner: the skip link only does its job if
          it is the first thing Tab reaches (F-013). */}
      <SkipLink />
      <GlobalBanner />
      {/* One sidebar, two presentations: a column when the shell is wide
          enough, otherwise a drawer behind the panel's menu button. Both
          render the same <SidebarNav>, so the phone never gets a nav that
          quietly drifts from the desktop one. */}
      {/* delayDuration 0: in the rail a tooltip is the label, not a hint. */}
      <TooltipProvider delayDuration={0}>
        <AppShell
          collapsed={sidebar.collapsed}
          sidebar={
            <SidebarNav
              modules={modules}
              inSettings={inSettings}
              onOpenPalette={openPalette}
              collapsed={sidebar.collapsed}
            />
          }
        >
          <ShellPanel
            top={
              <>
                <Sheet open={navOpen} onOpenChange={setNavOpen}>
                  <SheetTrigger
                    data-testid="nav-drawer-trigger"
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
                    {/* Radix requires a title on every dialog for the accessible
                      name; the drawer shows its own header, so this one is
                      visually hidden rather than duplicated on screen. */}
                    <SheetTitle className="sr-only">
                      {t("common.nav.menu")}
                    </SheetTitle>
                    <SidebarNav
                      modules={modules}
                      inSettings={inSettings}
                      onOpenPalette={openPalette}
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
                {/* Only where the sidebar is a column; on narrow screens the
                  drawer trigger above takes its place. */}
                <RailTooltip label={toggleLabel} enabled>
                  <button
                    type="button"
                    data-testid="sidebar-toggle"
                    aria-expanded={!sidebar.collapsed}
                    aria-keyshortcuts="["
                    aria-label={toggleLabel}
                    onClick={sidebar.toggle}
                    className="-ms-2 hidden rounded-md p-2 text-muted-foreground hover:bg-surface-sunken hover:text-foreground @3xl/shell:inline-flex"
                  >
                    <PanelLeft className="size-4" aria-hidden="true" />
                  </button>
                </RailTooltip>
                <p className="flex-1 truncate">{pageTitle}</p>
              </>
            }
          >
            {/* tabIndex={-1} so the skip link actually moves focus here.
              Without it the fragment jump scrolls the viewport but leaves
              focus on <body>, and the next Tab starts from the top of the
              document again -- the exact trap the link exists to avoid. */}
            <main
              id={MAIN_CONTENT_ID}
              tabIndex={-1}
              className="flex-1 focus:outline-none"
            >
              {children}
            </main>
          </ShellPanel>
        </AppShell>
      </TooltipProvider>
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
