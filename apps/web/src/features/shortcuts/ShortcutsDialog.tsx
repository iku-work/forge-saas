import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@forge/ui";

import {
  ALL_SHORTCUTS,
  isAppleDevice,
  renderKey,
  SHORTCUT_CATEGORIES,
  type ShortcutCategory,
} from "./registry";

function Keys({ keys, apple }: { keys: readonly string[]; apple: boolean }) {
  return (
    <span className="flex items-center gap-1">
      {keys.map((key) => (
        <kbd
          key={key}
          className="inline-flex min-h-6 min-w-6 items-center justify-center rounded border bg-muted px-1.5 text-caption font-medium text-muted-foreground"
        >
          {renderKey(key, apple)}
        </kbd>
      ))}
    </span>
  );
}

/**
 * The `?` cheat sheet (F-012).
 *
 * Rendered from the same registry the handler reads, so a shortcut cannot be
 * documented without working or work without being documented.
 */
export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const apple = isAppleDevice();

  const byCategory = SHORTCUT_CATEGORIES.map((category: ShortcutCategory) => ({
    category,
    shortcuts: ALL_SHORTCUTS.filter(
      (shortcut) => shortcut.category === category,
    ),
  })).filter((group) => group.shortcuts.length > 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="shortcuts-dialog">
        <DialogHeader>
          <DialogTitle>{t("common.shortcuts.title")}</DialogTitle>
          <DialogDescription>
            {t("common.shortcuts.description")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-6">
          {byCategory.map((group) => (
            <section key={group.category} className="space-y-2">
              <h3 className="text-caption font-medium uppercase tracking-wide text-muted-foreground">
                {t(`common.shortcuts.category.${group.category}`)}
              </h3>
              {/* A description list, not a table: this is term/definition
                  data, and <dl> gives a screen reader the pairing without a
                  table's row/column semantics, which would be a lie here. */}
              <dl className="space-y-1">
                {group.shortcuts.map((shortcut) => (
                  <div
                    key={shortcut.id}
                    className="flex items-center justify-between gap-4 py-1"
                  >
                    <dt className="text-body-sm">{t(shortcut.labelKey)}</dt>
                    <dd>
                      <Keys keys={shortcut.keys} apple={apple} />
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
