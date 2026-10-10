import type { ReactNode } from "react";

import { useDirection } from "@forge/hooks";
import { Tooltip, TooltipContent, TooltipTrigger } from "@forge/ui";

/**
 * In the collapsed rail a row is an icon alone, so its name appears as a
 * tooltip on hover and on keyboard focus. Expanded (and in the drawer, which
 * never collapses) the row shows its label and needs no tooltip.
 */
export function RailTooltip({
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
