# OWASP Top 10 (2021) — mitigation & test mapping

T-212. Every row below is machine-checked, not just documentation:

- Each listed test file carries `@pytest.mark.owasp("A0N")` on the test(s) that prove the mitigation.
- `backend/tests/security/test_owasp_mapping.py` fails the suite if any category below has no passing marked test, or if a path in this table no longer exists.

Update this table in the same PR that adds, moves, or removes a marked test.

| Category                              | Mitigation in this codebase                                                                          | Test file(s)                                                                                                                 |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| A01 Broken Access Control              | Org-scoped RBAC (`require_permission`) + a cross-tenant sweep over every org-scoped endpoint             | `backend/tests/security/test_tenant_isolation.py`, `backend/tests/test_permissions.py`                                       |
| A02 Cryptographic Failures              | Fernet-encrypted columns, HSTS, structured-log secret scrubbing                                          | `backend/tests/test_encrypted_type.py`, `backend/tests/test_middleware.py`, `backend/tests/test_logging.py`                  |
| A03 Injection                          | SQLAlchemy ORM (no raw string SQL) + Schemathesis contract fuzz + Semgrep's registry rules (`--config auto`, includes SQLi/raw-query detections) | `backend/tests/contract/test_schemathesis.py`, `backend/tests/test_ci_build_deploy.py`                                       |
| A04 Insecure Design                     | Per-IP/per-user rate limiting on sensitive routes + account lockout after repeated failed logins         | `backend/tests/test_rate_limit.py`, `backend/tests/test_auth_service_login.py`                                               |
| A05 Security Misconfiguration           | Security-headers middleware (CSP, X-Frame-Options, etc.) + a startup guard rejecting wildcard CORS outside dev | `backend/tests/test_middleware.py`, `backend/tests/test_config.py`                                                           |
| A06 Vulnerable and Outdated Components  | `pip-audit` + `pnpm audit` in the CI `security` job, gating the `build` job (not `continue-on-error`)     | `backend/tests/test_ci_build_deploy.py`                                                                                      |
| A07 Identification and Auth Failures    | Refresh-token rotation with reuse/theft detection, TOTP 2FA, account lockout                              | `backend/tests/test_auth_service_refresh.py`, `backend/tests/test_auth_totp.py`, `backend/tests/test_auth_service_login.py`  |
| A08 Software and Data Integrity Failures | Stripe webhook signature verification + `pnpm install --frozen-lockfile` everywhere in CI                 | `backend/tests/test_billing_webhooks.py`, `backend/tests/test_ci_build_deploy.py`                                            |
| A09 Security Logging and Monitoring Failures | Audit-log row written for every sensitive action, inside the caller's own transaction                 | `backend/tests/test_audit_service.py`                                                                                        |
| A10 Server-Side Request Forgery (SSRF)  | `validate_outbound_url()` rejects private/loopback/link-local/reserved IPs (literal or DNS-resolved), the cloud metadata address, and non-http(s) schemes | `backend/app/core/url_guard.py`, `backend/tests/test_url_guard.py`                                                           |

`url_guard.py` has no caller yet — Phase 4's outbound webhooks (T-32x) will be the first. It's built now so that feature starts from a safe default instead of retrofitting SSRF protection onto working code later.
