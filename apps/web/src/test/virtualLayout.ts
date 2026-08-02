// T-241: jsdom has no layout engine, so every element reports offsetHeight
// 0 and scrollTop 0, and @tanstack/react-virtual sizes its window off
// exactly those two numbers (virtual-core's getRect() reads offsetWidth /
// offsetHeight; observeElementOffset reads scrollTop). Left alone, a
// virtualizer under jsdom believes it has a zero-height viewport, which
// makes any assertion about "how many rows are on screen" meaningless.
//
// Rather than mocking useVirtualizer away (what NotificationBell.test.tsx
// does -- it isn't testing virtualization, so it renders every row), this
// gives the *real* virtualizer a believable layout: a fixed viewport
// height for the scroll container, a fixed height per row, and a scrollTo
// that actually moves the offset and fires a scroll event. That is what
// lets the DOM-node budget, the scroll-driven pagination, and the
// keyboard-across-a-boundary assertions exercise the shipping code path.
//
// Install it in a `beforeEach` and call the returned function in
// `afterEach`; the patches are on shared prototypes.

interface VirtualLayoutOptions {
  /** Height every [data-index] row reports. */
  rowHeight: number;
  /** Height every [data-virtual-list] scroll container reports. */
  viewportHeight: number;
}

const scrollOffsets = new WeakMap<Element, number>();

function isScrollContainer(element: Element): boolean {
  return element.hasAttribute("data-virtual-list");
}

function isRow(element: Element): boolean {
  return element.hasAttribute("data-index");
}

/** The virtualizer writes its total size onto the inner spacer's inline
 *  style, which is the only "content height" that exists in jsdom. */
function contentHeight(container: Element): number {
  const spacer = container.firstElementChild;
  if (!(spacer instanceof HTMLElement)) {
    return 0;
  }
  const parsed = Number.parseFloat(spacer.style.height);
  return Number.isNaN(parsed) ? 0 : parsed;
}

export function installVirtualLayout({
  rowHeight,
  viewportHeight,
}: VirtualLayoutOptions): () => void {
  const originalOffsetHeight = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetHeight",
  );
  const originalScrollTop = Object.getOwnPropertyDescriptor(
    Element.prototype,
    "scrollTop",
  );
  const originalClientHeight = Object.getOwnPropertyDescriptor(
    Element.prototype,
    "clientHeight",
  );
  const originalScrollHeight = Object.getOwnPropertyDescriptor(
    Element.prototype,
    "scrollHeight",
  );
  const originalScrollTo = Object.getOwnPropertyDescriptor(
    Element.prototype,
    "scrollTo",
  );
  // The virtualizer reaches its ResizeObserver through
  // `scrollElement.ownerDocument.defaultView`, which under vitest's jsdom
  // environment is not necessarily the same object as `globalThis` -- patch
  // both so it's found either way.
  const resizeObserverHosts: object[] = [globalThis];
  if (typeof window !== "undefined" && (window as object) !== globalThis) {
    resizeObserverHosts.push(window);
  }
  const originalResizeObservers = resizeObserverHosts.map((host) =>
    Object.getOwnPropertyDescriptor(host, "ResizeObserver"),
  );

  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get(this: HTMLElement) {
      if (isScrollContainer(this)) {
        return viewportHeight;
      }
      if (isRow(this)) {
        return rowHeight;
      }
      return 0;
    },
  });

  // virtual-core's getMaxScrollOffset() clamps every programmatic scroll
  // (scrollToIndex, which is what keyboard navigation drives) to
  // scrollHeight - clientHeight. Both are 0 in jsdom, so without these two
  // the virtualizer silently refuses to scroll anywhere at all.
  Object.defineProperty(Element.prototype, "clientHeight", {
    configurable: true,
    get(this: Element) {
      return isScrollContainer(this) ? viewportHeight : 0;
    },
  });

  Object.defineProperty(Element.prototype, "scrollHeight", {
    configurable: true,
    get(this: Element) {
      return isScrollContainer(this) ? contentHeight(this) : 0;
    },
  });

  Object.defineProperty(Element.prototype, "scrollTop", {
    configurable: true,
    get(this: Element) {
      return scrollOffsets.get(this) ?? 0;
    },
    set(this: Element, value: number) {
      scrollOffsets.set(this, value);
    },
  });

  Object.defineProperty(Element.prototype, "scrollTo", {
    configurable: true,
    writable: true,
    value(this: Element, ...args: [ScrollToOptions] | [number, number]) {
      const [first, second] = args;
      const top = typeof first === "number" ? (second ?? 0) : (first.top ?? 0);
      const max = Math.max(0, contentHeight(this) - viewportHeight);
      const clamped = Math.max(0, Math.min(top, max));
      if (scrollOffsets.get(this) === clamped) {
        return;
      }
      scrollOffsets.set(this, clamped);
      this.dispatchEvent(new Event("scroll"));
    },
  });

  // The virtualizer only needs the constructor to exist -- it re-reads sizes
  // on every scroll and measures rows through measureElement's offsetHeight
  // fallback, neither of which depends on a callback ever firing. Nothing in
  // these tests resizes anything.
  class StubResizeObserver {
    observe(): void {
      return undefined;
    }
    unobserve(): void {
      return undefined;
    }
    disconnect(): void {
      return undefined;
    }
  }
  for (const host of resizeObserverHosts) {
    Object.defineProperty(host, "ResizeObserver", {
      configurable: true,
      writable: true,
      value: StubResizeObserver,
    });
  }

  return () => {
    if (originalOffsetHeight) {
      Object.defineProperty(
        HTMLElement.prototype,
        "offsetHeight",
        originalOffsetHeight,
      );
    }
    if (originalScrollTop) {
      Object.defineProperty(Element.prototype, "scrollTop", originalScrollTop);
    }
    if (originalClientHeight) {
      Object.defineProperty(
        Element.prototype,
        "clientHeight",
        originalClientHeight,
      );
    }
    if (originalScrollHeight) {
      Object.defineProperty(
        Element.prototype,
        "scrollHeight",
        originalScrollHeight,
      );
    }
    if (originalScrollTo) {
      Object.defineProperty(Element.prototype, "scrollTo", originalScrollTo);
    } else {
      Reflect.deleteProperty(Element.prototype, "scrollTo");
    }
    resizeObserverHosts.forEach((host, index) => {
      const original = originalResizeObservers[index];
      if (original) {
        Object.defineProperty(host, "ResizeObserver", original);
      } else {
        Reflect.deleteProperty(host, "ResizeObserver");
      }
    });
  };
}

/** The scroll container VirtualList rendered. Scoped to `within` when the
 *  component renders into a portal (the notification popover). */
export function getVirtualScrollContainer(within: ParentNode = document): HTMLElement {
  const element = within.querySelector("[data-virtual-list]");
  if (!(element instanceof HTMLElement)) {
    throw new Error("No [data-virtual-list] scroll container in the DOM");
  }
  return element;
}

/** Scrolls to the very bottom of everything currently loaded. */
export function scrollToBottom(container: HTMLElement): void {
  container.scrollTo({ top: contentHeight(container) });
}
