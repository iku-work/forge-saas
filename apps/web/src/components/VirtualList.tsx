import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";

// T-241: the one virtualized-list primitive both unbounded lists (org
// members, notification centre) render through, so the DOM-node budget,
// the a11y semantics, and the scroll-driven pagination are implemented
// once rather than diverging per page.
//
// Three things here are load-bearing beyond "render fewer nodes":
//
//  1. Semantics. Absolutely-positioned rows inside a sized spacer have no
//     implicit list semantics for a screen reader, so the roles are
//     explicit: role="list" on the spacer, role="listitem" on every row.
//  2. Keyboard. Rows carry a roving tabindex and arrow keys walk them. A
//     row that holds focus is force-mounted via `rangeExtractor` even when
//     it scrolls out of the visible range -- otherwise focus would land on
//     a node React is about to unmount and fall back to <body>.
//  3. Pagination. Scroll position *is* the pagination driver: when the
//     rendered range comes within LOAD_MORE_TRIGGER_ROWS of the loaded
//     count, the next cursor page is fetched. `requestedForCountRef`
//     makes that exactly one fetch per loaded count -- the effect re-runs
//     on every render (callers pass a fresh `onLoadMore` closure), and
//     `isLoadingMore` doesn't flip to true until after the fetch starts,
//     so without the guard the same page could be requested twice.

const OVERSCAN = 5;
const LOAD_MORE_TRIGGER_ROWS = 3;

export interface VirtualListProps<TItem> {
  items: TItem[];
  /** Stable identity per row -- keys the virtualizer's measurement cache,
   *  which matters for a list that prepends (a live notification pushed to
   *  the top shifts every index below it). */
  getItemKey: (item: TItem, index: number) => string;
  renderItem: (item: TItem, index: number) => ReactNode;
  /** Accessible name for the role="list" container. */
  label: string;
  estimateSize: number;
  /** Rows whose height varies with their content (the notification feed:
   *  title and body both wrap, the action link is optional) must be
   *  measured after mount so the offsets below them are right. Rows of a
   *  known fixed height (the member list) skip measurement entirely --
   *  that's one less ResizeObserver per row and one less synchronous
   *  re-render pass during commit. */
  dynamicRowHeight?: boolean;
  maxHeight: number;
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
  itemTestId?: string;
  /** Opts this scroll container into TanStack Router's scroll restoration
   *  (see lib/scrollRestorationIds.ts). */
  scrollRestorationId?: string;
  /** Restored scroll offset from a previous visit, so the first render
   *  already materialises the rows the user was looking at rather than
   *  rendering the top of the list and jumping. */
  initialOffset?: number;
  className?: string;
}

export function VirtualList<TItem>({
  items,
  getItemKey,
  renderItem,
  label,
  estimateSize,
  dynamicRowHeight = false,
  maxHeight,
  hasMore,
  isLoadingMore,
  onLoadMore,
  itemTestId,
  scrollRestorationId,
  initialOffset,
  className,
}: VirtualListProps<TItem>) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const focusedIndexRef = useRef(0);
  focusedIndexRef.current = focusedIndex;
  const pendingFocusRef = useRef(false);

  const itemsRef = useRef(items);
  itemsRef.current = items;

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    overscan: OVERSCAN,
    // react-virtual only needs flushSync to keep a just-measured row from
    // painting at its estimated offset for one frame. With fixed-height
    // rows there is no measurement, so the synchronous re-render (and
    // React's "flushSync was called from inside a lifecycle method"
    // warning that comes with it) buys nothing.
    useFlushSync: dynamicRowHeight,
    getItemKey: (index) => {
      const item = itemsRef.current[index];
      return item === undefined ? index : getItemKey(item, index);
    },
    rangeExtractor: (range) => {
      const indexes = new Set(defaultRangeExtractor(range));
      const focused = focusedIndexRef.current;
      if (focused >= 0 && focused < range.count) {
        indexes.add(focused);
      }
      return [...indexes].sort((a, b) => a - b);
    },
    ...(initialOffset === undefined ? {} : { initialOffset }),
  });

  const virtualItems = virtualizer.getVirtualItems();
  const lastRenderedIndex = virtualItems[virtualItems.length - 1]?.index ?? -1;

  const requestedForCountRef = useRef(-1);
  useEffect(() => {
    if (!hasMore || isLoadingMore) {
      return;
    }
    if (lastRenderedIndex < items.length - 1 - LOAD_MORE_TRIGGER_ROWS) {
      return;
    }
    if (requestedForCountRef.current === items.length) {
      return;
    }
    requestedForCountRef.current = items.length;
    onLoadMore();
  }, [hasMore, isLoadingMore, lastRenderedIndex, items.length, onLoadMore]);

  const moveFocusTo = useCallback(
    (index: number) => {
      const clamped = Math.max(0, Math.min(index, items.length - 1));
      pendingFocusRef.current = true;
      setFocusedIndex(clamped);
      virtualizer.scrollToIndex(clamped);
    },
    [items.length, virtualizer],
  );

  // No dependency array: the row that should take focus may only mount on a
  // later render (once scrollToIndex has moved the range), so this retries
  // after every render until it finds the node. `pendingFocusRef` keeps it
  // from stealing focus on renders nobody asked to move it.
  useEffect(() => {
    if (!pendingFocusRef.current) {
      return;
    }
    const node = scrollRef.current?.querySelector<HTMLElement>(
      `[data-index="${String(focusedIndex)}"]`,
    );
    if (!node) {
      return;
    }
    pendingFocusRef.current = false;
    node.focus();
  });

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    // Only a row itself drives arrow navigation. Inside a row's own
    // controls (the role menu, a link) the arrow keys belong to that
    // control, and the event bubbles up here regardless.
    const rowIndex = (event.target as HTMLElement).dataset.index;
    if (rowIndex === undefined) {
      return;
    }
    const current = Number(rowIndex);
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        moveFocusTo(current + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        moveFocusTo(current - 1);
        break;
      case "Home":
        event.preventDefault();
        moveFocusTo(0);
        break;
      case "End":
        event.preventDefault();
        moveFocusTo(items.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div
      ref={scrollRef}
      data-virtual-list=""
      {...(scrollRestorationId === undefined
        ? {}
        : { "data-scroll-restoration-id": scrollRestorationId })}
      className={`overflow-y-auto ${className ?? ""}`}
      style={{ maxHeight }}
      onKeyDown={handleKeyDown}
    >
      <div
        role="list"
        aria-label={label}
        className="relative w-full"
        style={{ height: virtualizer.getTotalSize() }}
      >
        {virtualItems.map((virtualItem) => {
          const item = items[virtualItem.index];
          if (item === undefined) {
            return null;
          }
          return (
            <div
              key={virtualItem.key}
              role="listitem"
              data-index={virtualItem.index}
              {...(itemTestId === undefined ? {} : { "data-testid": itemTestId })}
              tabIndex={virtualItem.index === focusedIndex ? 0 : -1}
              {...(dynamicRowHeight ? { ref: virtualizer.measureElement } : {})}
              className="absolute start-0 top-0 w-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
              style={{ transform: `translateY(${String(virtualItem.start)}px)` }}
            >
              {renderItem(item, virtualItem.index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
