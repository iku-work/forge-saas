import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({ useModules: vi.fn(), useOrg: vi.fn() }));
vi.mock("@forge/hooks", () => hooks);

const { FeedbackButton } = await import("./FeedbackButton");

const API = getApiBaseUrl();
const ORG_ID = "11111111-1111-1111-1111-111111111111";

function renderButton() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FeedbackButton />
    </QueryClientProvider>,
  );
}

describe("FeedbackButton", () => {
  beforeEach(() => {
    hooks.useModules.mockReturnValue({ admin: true });
    hooks.useOrg.mockReturnValue({ org: { id: ORG_ID } });
  });

  it("is not there without an admin area to read it", () => {
    hooks.useModules.mockReturnValue({ admin: false });
    renderButton();

    expect(screen.queryByTestId("feedback-button")).not.toBeInTheDocument();
  });

  it("sends the message with the page and the current org", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${API}/api/v1/feedback`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(
          envelope({ id: "fb-1", created_at: "2026-11-17T00:00:00Z" }),
        );
      }),
    );
    renderButton();

    await userEvent.click(screen.getByTestId("feedback-button"));
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await userEvent.selectOptions(
      screen.getByLabelText("What is it about?"),
      "idea",
    );
    await userEvent.type(
      screen.getByLabelText("Your message"),
      "Dark mode please",
    );
    await userEvent.click(send);

    await waitFor(() => {
      expect(screen.queryByTestId("feedback-dialog")).not.toBeInTheDocument();
    });
    expect(body).toEqual({
      category: "idea",
      message: "Dark mode please",
      page: window.location.pathname,
      org_id: ORG_ID,
    });
  });
});
