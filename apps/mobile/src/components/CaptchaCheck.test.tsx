/**
 * The mobile Turnstile check: Cloudflare's widget in a WebView, posting its
 * token back. The WebView is stubbed so the test can play Cloudflare's part.
 */
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

interface WebViewProps {
  source: { html: string; baseUrl: string };
  onMessage: (event: { nativeEvent: { data: string } }) => void;
}

const webview = vi.hoisted(() => ({ last: null as WebViewProps | null }));

vi.mock("react-native-webview", () => ({
  WebView: (props: WebViewProps) => {
    webview.last = props;
    return null;
  },
}));

// The real module pulls in native Expo storage; only the base URL matters here.
vi.mock("../lib/api", () => ({
  getApiBaseUrl: () => "https://api.example.test",
}));

const { CaptchaCheck } = await import("./CaptchaCheck");

function renderCheck() {
  const onToken = vi.fn();
  render(<CaptchaCheck siteKey="site-key-123" onToken={onToken} />);
  const props = webview.last;
  if (!props) throw new Error("WebView was not rendered");
  return { onToken, props };
}

describe("CaptchaCheck", () => {
  it("loads Turnstile for this site key from the API's origin", () => {
    const { props } = renderCheck();

    expect(props.source.html).toContain('"site-key-123"');
    expect(props.source.html).toContain(
      "https://challenges.cloudflare.com/turnstile/v0/api.js",
    );
    expect(props.source.baseUrl).toBe("https://api.example.test");
  });

  it("passes on the token the page posts", () => {
    const { onToken, props } = renderCheck();

    props.onMessage({ nativeEvent: { data: '{"token":"from-cloudflare"}' } });

    expect(onToken).toHaveBeenLastCalledWith("from-cloudflare");
  });

  it("clears the token when it expires or the message is garbled", () => {
    const { onToken, props } = renderCheck();

    props.onMessage({ nativeEvent: { data: '{"token":null}' } });
    expect(onToken).toHaveBeenLastCalledWith(null);
    props.onMessage({ nativeEvent: { data: "not json" } });
    expect(onToken).toHaveBeenLastCalledWith(null);
  });
});
