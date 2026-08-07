// Co-located with the route it tests, per expo-router's own recommendation
// (see AppLayout.test.tsx for why the filename must not start with "_layout").
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn().mockResolvedValue(null),
  setItemAsync: vi.fn().mockResolvedValue(undefined),
  deleteItemAsync: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("expo-router", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
// The avatar-upload path reaches expo-document-picker/file-system/
// image-picker, whose native modules don't exist under jsdom (same mocks as
// FilesScreen.test.tsx).
vi.mock("expo-document-picker", () => ({ getDocumentAsync: vi.fn() }));
vi.mock("expo-file-system", () => ({
  createUploadTask: vi.fn(),
  FileSystemUploadType: { BINARY_CONTENT: 0, MULTIPART: 1 },
  getInfoAsync: vi.fn(),
}));
vi.mock("expo-image-picker", () => ({
  launchCameraAsync: vi.fn(),
  launchImageLibraryAsync: vi.fn(),
  requestCameraPermissionsAsync: vi.fn(),
  requestMediaLibraryPermissionsAsync: vi.fn(),
}));
vi.mock("../../src/components/LanguagePicker", () => ({
  LanguagePicker: () => null,
}));

const mocks = vi.hoisted(() => ({
  useSupportSettings: vi.fn(),
  openSupport: vi.fn(),
}));
vi.mock("../../src/lib/config", () => ({
  useSupportSettings: mocks.useSupportSettings,
}));
vi.mock("../../src/lib/support", () => ({ openSupport: mocks.openSupport }));

const SettingsScreen = (await import("./settings")).default;

describe("SettingsScreen contact support (T-262)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useSupportSettings.mockReturnValue({
      tool: "none",
      url: "mailto:support@example.com",
    });
    mocks.openSupport.mockResolvedValue(true);
  });

  it("opens support with a translated subject", async () => {
    render(<SettingsScreen />);

    await userEvent.click(screen.getByText("Contact support"));

    await waitFor(() => {
      expect(mocks.openSupport).toHaveBeenCalledWith("Support request");
    });
    expect(screen.queryByText(/Couldn't open support/)).toBeNull();
  });

  it("explains itself when the device has nowhere to open support", async () => {
    mocks.openSupport.mockResolvedValue(false);

    render(<SettingsScreen />);
    await userEvent.click(screen.getByText("Contact support"));

    expect(
      await screen.findByText(/Couldn't open support on this device/),
    ).toBeTruthy();
  });

  it("renders no support entry until /config has resolved one", () => {
    // Empty url = "not resolved yet" (see config.ts's UNRESOLVED_SUPPORT) --
    // a button that can't go anywhere is worse than no button.
    mocks.useSupportSettings.mockReturnValue({ tool: "none", url: "" });

    render(<SettingsScreen />);

    expect(screen.queryByText("Contact support")).toBeNull();
  });
});
