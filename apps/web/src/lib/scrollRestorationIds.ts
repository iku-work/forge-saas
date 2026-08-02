// T-241: stable `data-scroll-restoration-id` values for the virtualized
// scroll containers TanStack Router restores on back-navigation.
//
// The id has to be shared by two places that can't import each other's
// component: the route file (which reads the saved entry via
// useElementScrollRestoration -- a hook that needs a RouterProvider, so it
// stays in the route rather than the page component) and the VirtualList
// that stamps the attribute onto its scroll element. Router core matches
// the two by the `[data-scroll-restoration-id="..."]` selector, so a typo
// on either side silently degrades to "no restoration" -- hence one
// constant rather than two literals.
export const MEMBERS_LIST_SCROLL_ID = "members-list";
