# Mobile app (Expo / React Native)

## Local development

```
pnpm --filter mobile dev      # expo start
pnpm --filter mobile ios      # expo start --ios
pnpm --filter mobile android  # expo start --android
```

By default the app talks to `http://localhost:8000` (see `src/lib/api.ts`).
The iOS Simulator shares the host's network stack, so that default works
as-is against `make dev`'s compose stack. The Android emulator does not --
override it:

```
EXPO_PUBLIC_API_URL=http://10.0.2.2:8000 pnpm --filter mobile android
```

## E2E tests (Maestro)

`.maestro/` holds the three flows required by T-222: `login.yaml`,
`logout.yaml`, and `deep-link.yaml`. They assert on visible text/labels (this
app has no `testID`s yet -- see each component's `accessibilityLabel`), not
on timing, and they run against a real backend, not mocks.

### Prerequisites

1. Install the [Maestro CLI](https://maestro.mobile.dev/getting-started/installing-maestro).
2. Boot the dev stack and seed it from the repo root:
   ```
   make dev   # or: docker compose up -d && make migrate && make seed
   ```
   `make seed` creates the dedicated `mobile-e2e@example.com` account
   (`backend/app/core/seed.py`'s `MOBILE_E2E_EMAIL`) that `login.yaml` and
   `logout.yaml` sign in with, and the "Mobile E2E" org that
   `deep-link.yaml`'s setup script sends a real invitation from.
3. Build the app and install it on a booted iOS Simulator or Android
   emulator (a plain Expo Go install does not register the app's own
   `forge://` URL scheme reliably, which `deep-link.yaml` depends on):
   ```
   npx expo prebuild
   npx expo run:ios       # or: npx expo run:android
   ```

### Running the flows

```
cd apps/mobile
maestro test .maestro/                # all three flows
maestro test .maestro/login.yaml       # a single flow
```

`deep-link.yaml` additionally requires MailHog reachable at
`http://localhost:8025` (already part of `make dev`'s stack) -- its setup
script logs in as the seeded org owner, sends a real invitation through the
backend's invite endpoint, then polls MailHog for the resulting email and
extracts the token, the same way `apps/web/e2e/helpers/mailhog.ts` does for
the web Playwright suite. If your MailHog or backend isn't on the default
ports, override `MAILHOG_URL` / `BACKEND_URL` via `maestro test --env`.

### CI

`.github/workflows/maestro-mobile.yml` runs all three flows nightly (and on
`workflow_dispatch`) on a macOS runner against the iOS Simulator, not per-PR
-- booting a simulator and building the app is too slow to gate every push
on, and unlike the web Playwright suite these flows also depend on a real
native build rather than a dev server. Maestro Cloud was considered as a
device-farm alternative but skipped for now: the local-Simulator path needs
no extra account/billing setup and this suite doesn't yet need real-device
coverage (see also `docs/runbooks/phase2-verification.md`'s note on Phase 2
mobile testing being scoped to simulators/emulators, not physical devices).
