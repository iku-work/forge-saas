import { useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Keyboard, LogOut, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth } from "@forge/hooks";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@forge/ui";

import { useVisibleNavItems, type NavItem } from "../../lib/navigation";
import { useRouteLocale } from "../../lib/useRouteLocale";
import { useTheme } from "../../lib/useTheme";
import { isAppleDevice, renderKey } from "../shortcuts/registry";
import { useRegisteredCommands } from "./commands";
import { readRecentPaths } from "./recent";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onShowShortcuts: () => void;
  /** The pages to offer, when not the app's own (the admin console passes
   *  its pages). Recents are resolved against the same list. */
  navItems?: NavItem[];
}

/**
 * The ⌘K palette (F-005).
 *
 * Its navigation rows come from useVisibleNavItems(), the same source the
 * sidebar renders -- so the palette cannot offer a page the user's role,
 * plan or product configuration has turned off, and cannot miss one that was
 * just added.
 */
export function CommandPalette({
  open,
  onOpenChange,
  onShowShortcuts,
  navItems,
}: CommandPaletteProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const locale = useRouteLocale();
  const nav = useVisibleNavItems();
  const { resolved, setTheme } = useTheme();
  const { logout } = useAuth();
  const registered = useRegisteredCommands();
  const apple = isAppleDevice();

  // Read once per open rather than on every render: localStorage is
  // synchronous and this is in the render path of a list that re-filters on
  // each keystroke. `open` in the deps is what makes it re-read when the
  // palette is next shown, which is the only moment the value can have
  // changed in a way the user would notice.
  const recentPaths = useMemo(() => (open ? readRecentPaths() : []), [open]);

  const pages = navItems ?? nav.all;
  const byPath = new Map(pages.map((item) => [item.path, item]));
  // Resolved against the *current* nav, so a page that has since been hidden
  // (module turned off, role changed) silently drops out of recents rather
  // than offering a route that will bounce the user straight back.
  const recent = recentPaths
    .map((path) => byPath.get(path))
    .filter((item): item is NavItem => item !== undefined);

  const go = (item: NavItem): void => {
    onOpenChange(false);
    void navigate({ to: item.to, params: { locale } });
  };

  const renderNavItem = (item: NavItem, keyPrefix: string) => {
    const Icon = item.icon;
    const label = t(item.labelKey);
    return (
      <CommandItem
        key={`${keyPrefix}-${item.path}`}
        // cmdk matches on `value`, not on the rendered children. Without the
        // path in it, "settings" would not find "Profile" -- and the path is
        // exactly how a user who knows the app thinks about it.
        value={`${label} ${item.path}`}
        onSelect={() => {
          go(item);
        }}
        // Prefixed by group: the same page appears under both "Recent" and
        // "Go to", so a testid keyed on the path alone would match two
        // elements and every getByTestId for it would throw.
        data-testid={`palette-${keyPrefix}-${item.path}`}
      >
        <Icon className="size-4 shrink-0" aria-hidden="true" />
        {label}
      </CommandItem>
    );
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      label={t("common.palette.label")}
    >
      <CommandInput placeholder={t("common.palette.placeholder")} />
      <CommandList>
        <CommandEmpty>{t("common.palette.empty")}</CommandEmpty>

        {recent.length > 0 ? (
          <>
            <CommandGroup heading={t("common.palette.recent")}>
              {recent.map((item) => renderNavItem(item, "recent"))}
            </CommandGroup>
            <CommandSeparator />
          </>
        ) : null}

        <CommandGroup heading={t("common.palette.navigation")}>
          {pages.map((item) => renderNavItem(item, "nav"))}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading={t("common.palette.actions")}>
          <CommandItem
            value={`${t("common.palette.toggle_theme")} theme dark light appearance`}
            onSelect={() => {
              // Toggles between the two explicit values rather than cycling
              // through "system": someone reaching for this wants the other
              // one now, and "system" from here would look like it did
              // nothing whenever the OS already agreed.
              void setTheme(resolved === "dark" ? "light" : "dark");
            }}
            data-testid="palette-toggle-theme"
          >
            {resolved === "dark" ? (
              <Sun className="size-4 shrink-0" aria-hidden="true" />
            ) : (
              <Moon className="size-4 shrink-0" aria-hidden="true" />
            )}
            {t("common.palette.toggle_theme")}
          </CommandItem>

          <CommandItem
            value={`${t("common.shortcuts.title")} keyboard help`}
            onSelect={() => {
              onOpenChange(false);
              onShowShortcuts();
            }}
            data-testid="palette-shortcuts"
          >
            <Keyboard className="size-4 shrink-0" aria-hidden="true" />
            {t("common.shortcuts.title")}
            <CommandShortcut>{"?"}</CommandShortcut>
          </CommandItem>

          {registered.map((command) => (
            <CommandItem
              key={command.id}
              value={`${command.label} ${command.keywords ?? ""}`}
              onSelect={() => {
                onOpenChange(false);
                command.run();
              }}
            >
              {command.label}
            </CommandItem>
          ))}

          <CommandItem
            value={`${t("common.nav.logout")} logout sign out`}
            onSelect={() => {
              onOpenChange(false);
              void logout().then(() =>
                navigate({ to: "/$locale/login", params: { locale } }),
              );
            }}
            data-testid="palette-logout"
          >
            <LogOut className="size-4 shrink-0" aria-hidden="true" />
            {t("common.nav.logout")}
          </CommandItem>
        </CommandGroup>
      </CommandList>

      {/* A footer hint rather than a tooltip: the palette is the one surface
          where a user is already looking for what else they can do. */}
      <div className="flex items-center justify-end gap-2 border-t px-3 py-2 text-caption text-muted-foreground">
        <kbd className="rounded border bg-muted px-1.5 py-0.5">
          {renderKey("mod", apple)}
        </kbd>
        <kbd className="rounded border bg-muted px-1.5 py-0.5">{"K"}</kbd>
        <span>{t("common.palette.hint")}</span>
      </div>
    </CommandDialog>
  );
}
