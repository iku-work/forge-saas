import { client } from "./generated/client.gen";
import { authRetryInterceptor } from "./authInterceptor";
import { attachHttpStatus } from "./httpStatus";
import { orgIdRequestInterceptor } from "./orgInterceptor";

export * from "./generated";
export { client };
// Not re-exported by generated/index.ts — the plugin writes it under a
// "@tanstack" subpath that hey-api's entry-file generator doesn't traverse.
export * from "./generated/@tanstack/react-query.gen";
export {
  getAccessToken,
  setAccessToken,
  getApiBaseUrl,
  getCurrentOrgId,
  setCurrentOrgId,
  getImpersonatorId,
} from "./client";
export { authRetryInterceptor, onSessionExpired } from "./authInterceptor";
export { orgIdRequestInterceptor } from "./orgInterceptor";
export { HTTP_STATUS, httpStatusOf, attachHttpStatus } from "./httpStatus";
export * from "./admin";
// Re-exported for consumers (apps/mobile/src/lib/api.ts) that need their own
// Client instance instead of the singleton above -- that singleton is wired
// for the browser's cookie + import.meta.env world, which mobile has neither
// of (forgesaas-plan.html: "no cookies on mobile -- explicit token management").
export { createClient, createConfig } from "./generated/client";
export type { Client, Config } from "./generated/client";

// Registered once, here, rather than inside authInterceptor.ts/orgInterceptor.ts
// themselves -- neither file can import `client` without a circular import
// back through client.ts (which client.gen.ts's own createClientConfig
// depends on).
client.interceptors.response.use(authRetryInterceptor);
// The thrown value is the parsed error envelope, which carries no status --
// this is the one place the Response is still in scope. See httpStatus.ts.
client.interceptors.error.use(attachHttpStatus);
client.interceptors.request.use(orgIdRequestInterceptor);
