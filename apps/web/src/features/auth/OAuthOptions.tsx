import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useOAuthProviders } from "@forge/hooks";
import { Button, Separator } from "@forge/ui";

import { getOAuthStartUrl, OAUTH_PROVIDER_LABEL_KEYS } from "./oauth";

// The "or continue with" block. Shared by the login and sign-up pages, because
// the same button does both: a first sign-in with a provider creates the account.
export function OAuthOptions({ children }: { children?: ReactNode }) {
  const { t } = useTranslation();
  // UX-01: rendered from the providers /config says are actually configured,
  // not from `modules.oauth` -- see useOAuthProviders. The filter drops any
  // provider this build has no label for, so adding one server-side cannot
  // render a blank button before its string lands.
  const providers = useOAuthProviders().filter(
    (provider: string) => provider in OAUTH_PROVIDER_LABEL_KEYS,
  );

  if (providers.length === 0 && !children) {
    return null;
  }

  return (
    <>
      <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
        <Separator className="flex-1" />
        {t("auth.login.oauth_separator")}
        <Separator className="flex-1" />
      </div>
      <div className="grid gap-2">
        {providers.map((provider: string) => (
          <Button
            key={provider}
            type="button"
            variant="outline"
            size="lg"
            className="w-full"
            onClick={() => {
              window.location.href = getOAuthStartUrl(provider);
            }}
          >
            {t(OAUTH_PROVIDER_LABEL_KEYS[provider] ?? "")}
          </Button>
        ))}
        {children}
      </div>
    </>
  );
}
