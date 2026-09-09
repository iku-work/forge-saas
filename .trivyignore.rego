# Trivy ignore policy (T-350).
#
# The `.trivyignore.yaml` beside this file suppresses individual advisories.
# This one suppresses a *package at a version*, which is the right shape for
# exactly one situation in this repo and should not grow beyond it.
#
# node-tar 6.2.1 reaches this tree only through `@expo/cli>tar`, which
# pnpm-workspace.yaml pins to the 6.x line on purpose: @expo/cli@0.22.28's
# bundled tar.js does `_interopRequireDefault(require("tar")).default.extract`,
# and tar 7 sets `__esModule: true` while exporting no `default`. Verified
# against the installed 7.5.x -- `.default` is `undefined`, so `expo prebuild`
# dies with "Cannot read properties of undefined (reading 'extract')".
#
# **Why a policy rather than four more lines in the YAML file.** 6.x is
# end-of-life. Four advisories landed against it in a single afternoon
# (CVE-2026-59873, -23745, -23950, -24842) and more will follow, so
# enumerating them is a treadmill that reports a clean scan only until the
# next one is published. Suppressing the pinned version itself says the true
# thing once: *this exact version is knowingly carried, and nothing else is.*
#
# It is deliberately narrow. The match is on the version too, so the moment
# the pin moves -- which is the point of the expiry on the YAML entries -- this
# stops applying on its own and every advisory comes back. It also does not
# touch the `tar@7.x` every other package resolves to, which stays scanned.
#
# The real fix is upgrading @expo/cli, whose own tar range has moved past 7.
# Patching the installed copy was considered and rejected on evidence: its two
# call sites pass `extract({ onentry, strip, filter, cwd })` and tar 7 renamed
# `onentry` to `onReadEntry`, so the interop shim alone would leave the entry
# resolver silently not running during template extraction -- a quiet change in
# archive handling, unverifiable without a mobile toolchain, and worse than
# this suppression.
#
# What is actually exposed: archive extraction during `expo prebuild`, on a
# developer's machine, over archives Expo itself fetched. Not a request path,
# and not reachable from the deployed product.
package trivy

import rego.v1

default ignore := false

ignore if {
	input.PkgName == "tar"
	input.InstalledVersion == "6.2.1"
}
