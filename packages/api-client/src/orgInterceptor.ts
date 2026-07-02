import { getCurrentOrgId } from "./client";

const ORG_HEADER = "X-Org-ID";

// Registered as a request interceptor (see index.ts) so every generated SDK
// call carries the caller's currently selected organization automatically --
// @forge/hooks's useOrg is the only thing that ever calls setCurrentOrgId,
// and individual call sites never need to pass the header themselves.
// Returns a clone rather than mutating `request.headers` in place to match
// the immutable-looking style of the response interceptor in
// authInterceptor.ts, which has to clone anyway since it builds a retry
// request from scratch.
export function orgIdRequestInterceptor(request: Request): Request {
  const orgId = getCurrentOrgId();
  if (!orgId) {
    return request;
  }
  const headers = new Headers(request.headers);
  headers.set(ORG_HEADER, orgId);
  return new Request(request, { headers });
}
