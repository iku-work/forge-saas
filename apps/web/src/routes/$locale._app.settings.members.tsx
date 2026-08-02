import {
  createFileRoute,
  useElementScrollRestoration,
} from "@tanstack/react-router";

import { MembersSettingsPage } from "../features/settings/MembersSettingsPage";
import { MEMBERS_LIST_SCROLL_ID } from "../lib/scrollRestorationIds";

// T-241: the member list virtualizes, so restoring the scroll offset alone
// isn't enough -- on a back-navigation the browser would scroll a container
// whose rows for that offset haven't been rendered yet. Reading the saved
// entry here and handing it to the virtualizer as `initialOffset` makes the
// very first render materialise the right rows.
//
// useElementScrollRestoration needs a RouterProvider above it, which is why
// it lives in the route rather than in MembersSettingsPage -- that keeps the
// page component renderable on its own in unit tests.
function MembersRoute() {
  const entry = useElementScrollRestoration({ id: MEMBERS_LIST_SCROLL_ID });
  return (
    <MembersSettingsPage
      {...(entry?.scrollY === undefined
        ? {}
        : { initialScrollOffset: entry.scrollY })}
    />
  );
}

export const Route = createFileRoute("/$locale/_app/settings/members")({
  component: MembersRoute,
});
