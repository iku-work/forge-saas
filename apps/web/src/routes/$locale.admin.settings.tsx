import { createFileRoute } from "@tanstack/react-router";

import { AdminProductSettingsPage } from "../features/admin/AdminProductSettingsPage";

// T-263 specified this page as "/_admin/settings/product". It lands at
// /$locale/admin/settings instead, to match the admin route tree T-143
// actually built (a real, locale-prefixed "/admin" path, not a pathless
// `_admin` layout) -- and without the trailing "/product", since there is one
// settings page on this surface and a segment that never varies is a segment
// nobody needs to type.
export const Route = createFileRoute("/$locale/admin/settings")({
  component: AdminProductSettingsPage,
});
