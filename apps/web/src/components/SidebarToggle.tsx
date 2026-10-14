import { PanelLeft } from "lucide-react";
import { useTranslation } from "react-i18next";

import { RailTooltip } from "./RailTooltip";

/**
 * Collapses and expands the sidebar, from inside it: at the end of the top
 * row when expanded, under the logo in the rail. Desktop column only -- the
 * phone drawer never collapses, so it never renders one.
 */
export function SidebarToggle({
  collapsed,
  onToggle,
  testId,
}: {
  collapsed: boolean;
  onToggle: () => void;
  testId: string;
}) {
  const { t } = useTranslation();
  const label = t(
    collapsed ? "common.nav.expand_sidebar" : "common.nav.collapse_sidebar",
  );
  return (
    <RailTooltip label={label} enabled>
      <button
        type="button"
        data-testid={testId}
        aria-expanded={!collapsed}
        aria-keyshortcuts="["
        aria-label={label}
        onClick={onToggle}
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-data-[collapsed]/shell:size-10"
      >
        <PanelLeft className="size-4" aria-hidden="true" />
      </button>
    </RailTooltip>
  );
}
