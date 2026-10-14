import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, LogOut, Menu, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@forge/hooks";
import {
  AppShell,
  ShellLabel,
  ShellNavItem,
  ShellPanel,
  ShellSearchButton,
  ShellSidebar,
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
  Toaster,
  TooltipProvider,
} from "@forge/ui";

import { GlobalBanner } from "../components/GlobalBanner";
import { RailTooltip } from "../components/RailTooltip";
import { SidebarToggle } from "../components/SidebarToggle";
import { MAIN_CONTENT_ID, SkipLink } from "../components/SkipLink";
import { CommandPalette } from "../features/command-palette/CommandPalette";
import { isAppleDevice, renderChord } from "../features/shortcuts/registry";
import { ShortcutsDialog } from "../features/shortcuts/ShortcutsDialog";
import { useGlobalShortcuts } from "../features/shortcuts/useGlobalShortcuts";
import { ADMIN_NAV_ITEMS, type NavItem } from "../lib/navigation";
import { useRouteLocale } from "../lib/useRouteLocale";
import { useSidebarCollapsed } from "../lib/useSidebarCollapsed";

// The palette offers the admin pages and the way back to the app.
const BACK_TO_APP: NavItem = {
  to: "/$locale",
  path: "/",
  labelKey: "common.nav.back_to_app",
  icon: ArrowLeft,
};
const PALETTE_ITEMS: NavItem[] = [...ADMIN_NAV_ITEMS, BACK_TO_APP];

function AdminSidebar({
  onNavigate,
  onOpenPalette,
  collapsed = false,
  toggle,
}: {
  onNavigate?: () => void;
  onOpenPalette: () => void;
  /** Rail mode: tooltips on. The drawer copy never passes it. */
  collapsed?: boolean;
  /** The collapse button, ending the top row. Desktop column only. */
  toggle?: ReactNode;
}) {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const locale = useRouteLocale();

  const handleLogout = async (): Promise<void> => {
    await logout();
    await navigate({ to: "/$locale/login", params: { locale } });
  };

  const header = (
    <div className="flex flex-col items-start gap-2 px-2.5 pb-2 pt-1.5 group-data-[collapsed]/shell:items-center group-data-[collapsed]/shell:px-0">
      <div className="flex w-full items-center gap-2.5 text-sm font-semibold group-data-[collapsed]/shell:flex-col">
        <span
          aria-hidden="true"
          className="grid size-[1.375rem] shrink-0 place-items-center rounded-sm bg-primary text-xs font-bold text-primary-foreground"
        >
          {t("common.app.name").trim().charAt(0).toUpperCase()}
        </span>
        <ShellLabel>{t("common.admin.console")}</ShellLabel>
        <span className="ms-auto group-data-[collapsed]/shell:ms-0">
          {toggle}
        </span>
      </div>
      {/* T-143 / ticket 12: support staff must never mistake this surface
          for the ordinary app while acting on someone else's data. A word
          and an icon, in the warning colours -- never the accent, and never
          colour alone. In the rail the word becomes the icon's accessible
          name, so it is still there for a screen reader. */}
      <span
        data-testid="admin-marker"
        className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-2 py-0.5 text-xs font-semibold text-warning group-data-[collapsed]/shell:px-1.5"
      >
        <ShieldAlert className="size-3.5" aria-hidden="true" />
        <ShellLabel>{t("common.admin.badge")}</ShellLabel>
      </span>
      <RailTooltip label={t("common.palette.trigger")} enabled={collapsed}>
        <ShellSearchButton
          aria-keyshortcuts="Meta+K Control+K"
          label={t("common.palette.trigger")}
          shortcut={renderChord(["mod", "K"], isAppleDevice())}
          onClick={onOpenPalette}
        />
      </RailTooltip>
    </div>
  );

  const footer = (
    <>
      <RailTooltip label={t("common.nav.back_to_app")} enabled={collapsed}>
        <ShellNavItem asChild>
          <Link
            to="/$locale"
            params={{ locale }}
            data-testid="admin-back-to-app"
          >
            <ArrowLeft aria-hidden="true" />
            <ShellLabel>{t("common.nav.back_to_app")}</ShellLabel>
          </Link>
        </ShellNavItem>
      </RailTooltip>
      {user ? (
        <>
          <p className="truncate px-2.5 pt-2 text-xs text-muted-foreground group-data-[collapsed]/shell:sr-only">
            {user.email}
          </p>
          <RailTooltip label={t("common.nav.logout")} enabled={collapsed}>
            <ShellNavItem
              type="button"
              onClick={() => {
                void handleLogout();
              }}
            >
              <LogOut aria-hidden="true" />
              <ShellLabel>{t("common.nav.logout")}</ShellLabel>
            </ShellNavItem>
          </RailTooltip>
        </>
      ) : null}
    </>
  );

  return (
    <ShellSidebar header={header} footer={footer}>
      <nav className="flex flex-col gap-0.5" onClick={onNavigate}>
        {ADMIN_NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <RailTooltip
              key={item.to}
              label={t(item.labelKey)}
              enabled={collapsed}
            >
              <ShellNavItem asChild>
                <Link
                  to={item.to}
                  params={{ locale }}
                  activeOptions={{ exact: item.to === "/$locale/admin" }}
                >
                  <Icon aria-hidden="true" />
                  <ShellLabel>{t(item.labelKey)}</ShellLabel>
                </Link>
              </ShellNavItem>
            </RailTooltip>
          );
        })}
      </nav>
    </ShellSidebar>
  );
}

/**
 * The superadmin console in the same sidebar-and-panel shell as the app
 * (visual overhaul, ticket 12), told apart by the Admin marker at the top of
 * the sidebar rather than by a second interface to learn. It collapses to a
 * rail with `[` like the app (the same per-device setting), and ⌘K opens the
 * palette over the admin pages.
 */
export function AdminLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const locale = useRouteLocale();
  const [navOpen, setNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
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
      // `g` sequences name the app's pages; here they reach only the ones
      // the admin console has, and do nothing otherwise.
      const target = PALETTE_ITEMS.find((item) => item.path === path);
      if (target) {
        void navigate({ to: target.to, params: { locale } });
      }
    },
  });

  const openPalette = (): void => {
    setNavOpen(false);
    setPaletteOpen(true);
  };

  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <SkipLink />
      {/* T-263: the operator sees the banner their users see -- a banner that
          is only visible outside the admin surface is one nobody notices they
          left up. */}
      <GlobalBanner />
      {/* delayDuration 0: in the rail a tooltip is the label, not a hint. */}
      <TooltipProvider delayDuration={0}>
        <AppShell
          collapsed={sidebar.collapsed}
          sidebar={
            <AdminSidebar
              onOpenPalette={openPalette}
              collapsed={sidebar.collapsed}
              toggle={
                <SidebarToggle
                  collapsed={sidebar.collapsed}
                  onToggle={sidebar.toggle}
                  testId="admin-sidebar-toggle"
                />
              }
            />
          }
        >
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
                      onOpenPalette={openPalette}
                      onNavigate={() => {
                        setNavOpen(false);
                      }}
                    />
                  </SheetContent>
                </Sheet>
                <p className="flex-1 truncate text-sm font-semibold text-foreground">
                  {t("common.admin.console")}
                </p>
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
      </TooltipProvider>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onShowShortcuts={() => {
          setShortcutsOpen(true);
        }}
        navItems={PALETTE_ITEMS}
      />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      <Toaster />
    </div>
  );
}
