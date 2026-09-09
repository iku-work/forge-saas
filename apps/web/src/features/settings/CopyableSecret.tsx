import { useState } from "react";

import { Button } from "@forge/ui";

/**
 * A credential shown once, with a copy button (T-313, reused by T-322).
 *
 * Shared by the API-key and webhook-secret dialogs because both have the same
 * contract and the same failure mode: the value is displayed exactly once and
 * the server keeps no copy, so "copy" has to actually work — including where
 * `navigator.clipboard` does not exist (an insecure origin, an old browser,
 * jsdom). The `<code>` block is therefore selectable text first and a button
 * second: if the copy fails, the value is still right there to select by
 * hand, and the button says so rather than silently doing nothing.
 *
 * `type="button"` matters — this renders inside dialogs that sit next to
 * forms, and a default-type button inside one submits it.
 */
export function CopyableSecret({
  value,
  testId,
  copyLabel,
  copiedLabel,
}: {
  value: string;
  testId: string;
  copyLabel: string;
  copiedLabel: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (): void => {
    // The DOM lib types `navigator.clipboard` as always present; it is not.
    // It is undefined on an insecure origin and in jsdom, and an unguarded
    // call throws a TypeError that would take the dialog down with it -- so
    // the type is widened here to the truth rather than trusted.
    const { clipboard } = navigator as { clipboard?: Clipboard };
    if (clipboard === undefined) {
      return;
    }
    void clipboard
      .writeText(value)
      .then(() => {
        setCopied(true);
      })
      .catch(() => {
        // Deliberately silent: the value is on screen and selectable, so a
        // failed copy is an inconvenience rather than something to interrupt
        // somebody with while they are holding a credential.
        setCopied(false);
      });
  };

  return (
    <div className="flex items-center gap-2">
      <code
        className="flex-1 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs"
        data-testid={testId}
      >
        {value}
      </code>
      <Button type="button" variant="outline" size="sm" onClick={handleCopy}>
        {copied ? copiedLabel : copyLabel}
      </Button>
    </div>
  );
}
