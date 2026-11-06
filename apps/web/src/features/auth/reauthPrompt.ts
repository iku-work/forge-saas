/**
 * The eager half of ReauthDialog, small enough for the initial bundle.
 *
 * The dialog itself is lazy-loaded (bundle-budget). If an action needs a
 * recent sign-in before it has arrived, the question waits here, and the
 * dialog opens with it as soon as it mounts, so the first click still works.
 */
type Answer = (token: string | null) => void;

let waiting: Answer | null = null;
let listener: ((answer: Answer) => void) | null = null;

// Registered with the api-client's setReauthHandler.
export function requestReauth(): Promise<string | null> {
  return new Promise((resolve) => {
    if (listener) {
      listener(resolve);
    } else {
      waiting = resolve;
    }
  });
}

// ReauthDialog subscribes on mount; a question asked before then is handed
// over at once.
export function onReauthRequest(handle: (answer: Answer) => void): () => void {
  listener = handle;
  if (waiting) {
    const answer = waiting;
    waiting = null;
    handle(answer);
  }
  return () => {
    if (listener === handle) {
      listener = null;
    }
  };
}
