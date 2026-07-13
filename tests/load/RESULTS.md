# Load test results (T-223)

Trend log, one line appended per nightly run by
`.github/workflows/load-test.yml`. Not hand-edited except for this header --
see `README.md` for what each script models and why this runs nightly
instead of per-PR.

| Date | Commit | Script | p95 (ms) | Error rate | Result |
| ---- | ------ | ------ | -------- | ---------- | ------ |
| 2026-07-13 | 2396ada | login-burst | - | - | FAIL |
| 2026-07-13 | 2396ada | browse | - | - | PASS |
| 2026-07-13 | 2396ada | presign | - | - | PASS |
| 2026-07-13 | 2396ada | mixed | - | - | FAIL |
