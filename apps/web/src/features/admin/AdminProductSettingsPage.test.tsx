import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type {
  AdminProductSettings,
  ProductSettingsValue,
} from "@forge/api-client";

import { envelope, makeUser } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();
const superadmin = makeUser(41, { is_superadmin: true });

// Mirrors what the backend's registry (app.modules.product_settings.keys)
// actually ships, because the page builds its form from this rather than from
// a hardcoded list -- a fixture that invented its own keys would prove nothing.
function makeSettings(
  settings: Record<string, ProductSettingsValue> = {},
): AdminProductSettings {
  return {
    settings,
    well_known: [
      {
        key: "maintenance_mode",
        kind: "bool",
        description: "Return 503 from every non-admin, non-health endpoint.",
        default: false,
      },
      {
        key: "maintenance_message",
        kind: "str",
        description: "What users see while maintenance mode is on.",
        default: "",
      },
      {
        key: "signup_disabled",
        kind: "bool",
        description: "Reject self-serve registration.",
        default: false,
      },
      {
        key: "global_banner",
        kind: "banner",
        description: "An announcement rendered above the app shell.",
        default: null,
      },
      {
        key: "emergency_kill_switch",
        kind: "kill_switch",
        description: "Per-module off switch.",
        default: {},
      },
    ],
    killable_modules: ["billing", "file_storage", "notifications"],
    updated_at: null,
    updated_by: null,
  };
}

function stubSettings(initial: Record<string, ProductSettingsValue> = {}): {
  patches: Record<string, ProductSettingsValue>[];
  puts: Record<string, ProductSettingsValue>[];
} {
  const patches: Record<string, ProductSettingsValue>[] = [];
  const puts: Record<string, ProductSettingsValue>[] = [];
  let current = { ...initial };
  server.use(
    http.get(`${API}/api/v1/admin/settings/product`, () =>
      HttpResponse.json(envelope(makeSettings(current))),
    ),
    http.patch(`${API}/api/v1/admin/settings/product`, async ({ request }) => {
      const body = (await request.json()) as {
        settings: Record<string, ProductSettingsValue>;
      };
      patches.push(body.settings);
      current = Object.fromEntries(
        Object.entries({ ...current, ...body.settings }).filter(
          ([, value]) => value !== null,
        ),
      );
      return HttpResponse.json(envelope(makeSettings(current)));
    }),
    http.put(`${API}/api/v1/admin/settings/product`, async ({ request }) => {
      const body = (await request.json()) as {
        settings: Record<string, ProductSettingsValue>;
      };
      puts.push(body.settings);
      current = { ...body.settings };
      return HttpResponse.json(envelope(makeSettings(current)));
    }),
  );
  return { patches, puts };
}

describe("AdminProductSettingsPage", () => {
  it("renders each well-known key's server-supplied description", async () => {
    stubSettings();

    renderApp({ initialPath: "/admin/settings", user: superadmin });

    // Proof the form is generated from the backend's registry, not a copy of
    // it: this text exists nowhere in the frontend.
    await screen.findByText(
      "Return 503 from every non-admin, non-health endpoint.",
    );
    expect(screen.getByText("Per-module off switch.")).toBeInTheDocument();
  });

  it("offers a kill-switch toggle only for the modules the server says are killable", async () => {
    stubSettings();

    renderApp({ initialPath: "/admin/settings", user: superadmin });
    await screen.findByLabelText("billing");

    expect(screen.getByLabelText("notifications")).toBeInTheDocument();
    // `gdpr` mounts under a path shared with auth, so the server never offers
    // it -- and the page must not invent it.
    expect(screen.queryByLabelText("gdpr")).not.toBeInTheDocument();
  });

  it("sends null rather than false when a switch is turned off", async () => {
    // The distinction that matters in the audit diff: an unset key and a key
    // stored as `false` look identical in this form and read very differently
    // in the trail an incident review reads.
    const { patches } = stubSettings({ maintenance_mode: true });

    renderApp({ initialPath: "/admin/settings", user: superadmin });
    const checkbox = await screen.findByLabelText("Maintenance mode");
    await waitFor(() => {
      expect(checkbox).toBeChecked();
    });

    await userEvent.click(checkbox);
    await userEvent.click(screen.getByTestId("save-product-settings"));

    await waitFor(() => {
      expect(patches).toHaveLength(1);
    });
    expect(patches[0]?.maintenance_mode).toBeNull();
  });

  it("warns loudly while maintenance mode is on", async () => {
    stubSettings({ maintenance_mode: true });

    renderApp({ initialPath: "/admin/settings", user: superadmin });

    await screen.findByText(/Maintenance mode is ON/);
  });

  it("submits a banner as the object shape the server validates", async () => {
    const { patches } = stubSettings();

    renderApp({ initialPath: "/admin/settings", user: superadmin });
    await userEvent.click(await screen.findByLabelText("Show a global banner"));
    await userEvent.type(
      screen.getByLabelText("Banner message"),
      "Degraded performance",
    );
    await userEvent.click(screen.getByTestId("save-product-settings"));

    await waitFor(() => {
      expect(patches).toHaveLength(1);
    });
    expect(patches[0]?.global_banner).toEqual({
      type: "info",
      message: "Degraded performance",
      dismissible: true,
    });
  });

  it("rejects malformed JSON in the raw editor without calling the server", async () => {
    const { puts } = stubSettings();

    renderApp({ initialPath: "/admin/settings", user: superadmin });
    const editor = await screen.findByLabelText("Raw JSON");
    await userEvent.clear(editor);
    await userEvent.type(editor, "{{not json");
    await userEvent.click(screen.getByTestId("save-raw-product-settings"));

    await screen.findByText("That isn't valid JSON.");
    expect(puts).toHaveLength(0);
  });

  it("shows the server's reason when a value is refused", async () => {
    stubSettings();
    server.use(
      http.patch(`${API}/api/v1/admin/settings/product`, () =>
        HttpResponse.json(
          {
            data: null,
            error: {
              code: "unprocessable_entity",
              message_key: "errors.invalid_product_setting",
              message: "maintenance_mode must be a boolean",
              details: { reason: "maintenance_mode must be a boolean" },
            },
            meta: { request_id: "r1", version: "1.0.0" },
          },
          { status: 422 },
        ),
      ),
    );

    renderApp({ initialPath: "/admin/settings", user: superadmin });
    await userEvent.click(await screen.findByLabelText("Maintenance mode"));
    await userEvent.click(screen.getByTestId("save-product-settings"));

    await screen.findByText("That setting value isn't valid.");
  });
});
