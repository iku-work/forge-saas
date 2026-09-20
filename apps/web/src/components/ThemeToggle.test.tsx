import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { startThemeSync, THEME_STORAGE_KEY } from "../lib/theme";
import { server } from "../test/server";
import { ThemeToggle } from "./ThemeToggle";

const API = getApiBaseUrl();

let teardown: (() => void) | null = null;

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
  // jsdom has no matchMedia; the store falls back to light without one, which
  // is the state these assertions are written against.
  teardown = startThemeSync();
});

afterEach(() => {
  teardown?.();
  teardown = null;
});

describe("ThemeToggle", () => {
  it("renders one radio per preference, with the current one selected", () => {
    render(<ThemeToggle />);

    const group = screen.getByRole("radiogroup");
    expect(group).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    // "system" is the default for a user who has never chosen.
    expect(screen.getByTestId("theme-option-system")).toBeChecked();
  });

  it("applies the theme and persists it to the account", async () => {
    const patched = vi.fn();
    server.use(
      http.patch(`${API}/api/v1/auth/me`, async ({ request }) => {
        patched((await request.json()) as unknown);
        return HttpResponse.json({ data: { theme: "dark" } });
      }),
    );

    render(<ThemeToggle />);
    await userEvent.click(screen.getByTestId("theme-option-dark"));

    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(patched).toHaveBeenCalledWith({ theme: "dark" });
  });

  it("still applies the theme when the account cannot be updated", async () => {
    // Signed out, offline, or a 500. The theme is a display choice -- there
    // is nothing to roll back and nothing the user could do about it, so it
    // must not surface as an error or, worse, refuse to switch.
    server.use(http.patch(`${API}/api/v1/auth/me`, () => HttpResponse.error()));

    render(<ThemeToggle />);
    await userEvent.click(screen.getByTestId("theme-option-dark"));

    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("names the group for assistive technology", () => {
    // The three labels are "Light"/"Dark"/"System" -- without a group name a
    // screen reader announces "Dark, radio, 2 of 3" with no clue what is
    // being chosen.
    render(<ThemeToggle />);

    expect(
      screen.getByRole("radiogroup", { name: /appearance/i }),
    ).toBeInTheDocument();
  });
});
