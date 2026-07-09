"""Generates the pentest sensitive-endpoint inventory (T-214) from the live
route table instead of hand-maintaining a list that drifts from the code.

Two sources, merged:
  - the public OpenAPI schema (`fastapi.openapi.utils.get_openapi`), for
    every route FastAPI documents, filtered to the auth/billing/gdpr tags;
  - the admin router walked directly, since `app.modules.admin.router` sets
    `include_in_schema=False` (it's an internal support-ops surface, not a
    client-facing API) -- get_openapi alone would silently omit it, and
    admin routes (including impersonation) are exactly what a pentest scope
    most needs listed.

Run via `python -m app.core.security_endpoint_inventory` from `backend/`
(same prerequisite as `make seed`: a filled-in `.env`, since importing the
router tree touches `app.core.module_loader`). Prints a Markdown table to
stdout -- paste into docs/runbooks/pentest-prep.md when the route table
changes; this is a point-in-time snapshot, not a live CI check.
"""

from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app.api.v1 import build_router
from app.modules.admin.router import router as admin_router

_SENSITIVE_TAGS = {"auth", "billing", "gdpr"}


def _admin_rows() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for route in admin_router.routes:
        if not isinstance(route, APIRoute):
            continue
        category = "admin/impersonation" if "impersonate" in route.path else "admin"
        rows.extend(
            (method, f"/api/v1{route.path}", category)
            for method in sorted((route.methods or set()) - {"HEAD"})
        )
    return rows


def build_inventory() -> list[tuple[str, str, str]]:
    """Returns (method, path, category) rows for every sensitive endpoint."""
    router = build_router()
    schema = get_openapi(title="ForgeSaaS", version="0.1.0", routes=router.routes)
    rows: list[tuple[str, str, str]] = []
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            hit = set(op.get("tags", [])) & _SENSITIVE_TAGS
            if hit:
                rows.append((method.upper(), path, "/".join(sorted(hit))))
    rows.extend(_admin_rows())
    rows.sort(key=lambda r: (r[2], r[1], r[0]))
    return rows


def render_markdown(rows: list[tuple[str, str, str]]) -> str:
    lines = ["| Method | Path | Category |", "|---|---|---|"]
    lines.extend(f"| {method} | `{path}` | {category} |" for method, path, category in rows)
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_markdown(build_inventory()))  # noqa: T201 -- CLI tool, stdout is the product
