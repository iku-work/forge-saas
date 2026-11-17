import { useTranslation } from "react-i18next";
import { View } from "react-native";
import { WebView, type WebViewMessageEvent } from "react-native-webview";

import { getApiBaseUrl } from "../lib/api";

// The same Cloudflare script the web app loads, in a small embedded page.
// Turnstile checks the page's hostname against the ones allowed for the site
// key, so the page is given the API's origin: allow that host in the
// Turnstile dashboard next to the web app's (docs/runbooks/first-product.md).
function pageFor(siteKey: string, language: string): string {
  const post = (token: string) =>
    `function(t){window.ReactNativeWebView.postMessage(JSON.stringify({token:${token}}))}`;
  return `<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=go" async defer></script>
</head><body style="margin:0;display:flex;justify-content:center;background:transparent">
<div id="check"></div>
<script>function go(){turnstile.render('#check',{sitekey:${JSON.stringify(siteKey)},language:${JSON.stringify(language)},callback:${post("t")},'expired-callback':${post("null")},'error-callback':${post("null")}});}</script>
</body></html>`;
}

/**
 * Cloudflare Turnstile's check for the mobile app. Calls `onToken` with a
 * token once it passes, and with null when it expires or errors. A token is
 * single-use: remount this (change its `key`) after a submit.
 */
export function CaptchaCheck({
  siteKey,
  onToken,
}: {
  siteKey: string;
  onToken: (token: string | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const onMessage = (event: WebViewMessageEvent): void => {
    try {
      const message = JSON.parse(event.nativeEvent.data) as { token?: unknown };
      onToken(typeof message.token === "string" ? message.token : null);
    } catch {
      onToken(null);
    }
  };
  return (
    <View
      accessibilityLabel={t("auth.captcha.label")}
      accessibilityHint={t("auth.captcha.hint")}
      style={{ height: 72 }}
      testID="captcha-check"
    >
      <WebView
        source={{
          html: pageFor(siteKey, i18n.language),
          baseUrl: getApiBaseUrl(),
        }}
        onMessage={onMessage}
        originWhitelist={["https://*"]}
        javaScriptEnabled
        scrollEnabled={false}
        style={{ backgroundColor: "transparent" }}
      />
    </View>
  );
}
