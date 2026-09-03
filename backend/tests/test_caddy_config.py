import ast
import re
from pathlib import Path

from app.core.config import Settings

REPO_ROOT = Path(__file__).parent.parent.parent


def test_caddyfile_prod_redirects_http_to_https() -> None:
    content = (REPO_ROOT / "Caddyfile.prod").read_text()
    assert "redir https://" in content


def test_caddyfile_dev_uses_internal_tls() -> None:
    content = (REPO_ROOT / "Caddyfile").read_text()
    assert "tls internal" in content


def test_caddyfile_dev_redirects_http_to_https_with_301() -> None:
    content = (REPO_ROOT / "Caddyfile").read_text()
    assert "redir https://{host}{uri} 301" in content


def test_caddyfile_dev_serves_caddy_hostname_for_zap() -> None:
    # T-210: the ZAP baseline scan targets https://caddy from inside
    # forge-net, so that hostname needs its own tls cert and a default_sni
    # fallback for the scanner's SNI-less first probe (see Caddyfile).
    content = (REPO_ROOT / "Caddyfile").read_text()
    assert "localhost, caddy {" in content
    assert "default_sni caddy" in content


# A bare `respond /metrics 404` is not enough and these tests used to accept
# it. Caddy sorts `handle` ahead of `respond` in its default directive order,
# so once either file grew handle blocks the catch-all matched /metrics first
# and the respond never ran: both edges answered 200 with the SPA shell while
# this file reported them as blocking it. Grepping for the old string is what
# made that invisible for as long as it lasted.
#
# So the assertion is on the shape that actually works -- the rule inside a
# `handle`, which is mutually exclusive with the other handle blocks and wins
# by being written first. CI's module-matrix job makes the behavioural
# assertion, curling /metrics against a real Caddy.
_METRICS_BLOCK = "handle /metrics {"


def _first_handle_is_metrics(content: str) -> bool:
    """The rule only holds while it precedes the catch-all handle."""
    return content.index(_METRICS_BLOCK) < content.index("	handle {")


def test_caddyfile_prod_blocks_public_metrics_access() -> None:
    content = (REPO_ROOT / "Caddyfile.prod").read_text()

    assert _METRICS_BLOCK in content
    assert "respond 404" in content
    assert _first_handle_is_metrics(content)


def test_caddyfile_dev_blocks_public_metrics_access() -> None:
    content = (REPO_ROOT / "Caddyfile").read_text()

    assert _METRICS_BLOCK in content
    assert "respond 404" in content
    assert _first_handle_is_metrics(content)


def test_caddyfile_prod_strips_server_header() -> None:
    # T-225/ASVS V14.3.3: Uvicorn's default `Server: uvicorn` response
    # header shouldn't leak past the edge.
    content = (REPO_ROOT / "Caddyfile.prod").read_text()
    assert "-Server" in content


# ---------------------------------------------------------------------------
# T-307: apps/marketing on its own host.
# ---------------------------------------------------------------------------


def _compose(name: str) -> str:
    return (REPO_ROOT / name).read_text()


def test_both_caddyfiles_import_the_generated_marketing_block() -> None:
    # A *glob* import, and the shape matters more than it looks: a glob that
    # matches nothing is a warning in Caddy's log, while an `import` of a
    # missing file is a fatal config error. That difference is what lets a
    # product with `modules.marketing: false` -- and any stack that never
    # built the frontend, such as the ZAP baseline job -- boot an edge that
    # serves the app exactly as before.
    for name in ("Caddyfile", "Caddyfile.prod"):
        assert "import /etc/caddy/conf.d/*.caddy" in (REPO_ROOT / name).read_text()


def test_dev_compose_mounts_marketing_next_to_the_spa() -> None:
    content = _compose("docker-compose.yml")
    assert "- ./apps/marketing/dist:/srv/marketing:ro" in content
    assert "- ./apps/marketing/caddy/dev:/etc/caddy/conf.d:ro" in content
    # The app's own mount is untouched: the failure to avoid here is a
    # marketing module taking the product's edge with it.
    assert "- ./apps/web/dist:/srv/web:ro" in content


def test_prod_compose_mounts_the_production_marketing_block() -> None:
    content = _compose("docker-compose.prod.yml")
    assert "- ./apps/marketing/dist:/srv/marketing:ro" in content
    assert "- ./apps/marketing/caddy/prod:/etc/caddy/conf.d:ro" in content


def test_marketing_domain_is_declared_and_never_blank() -> None:
    # Caddy substitutes a set-but-empty env var as the empty string rather
    # than falling back to `{$MARKETING_DOMAIN:...}`, which leaves a site
    # block with no address -- and Caddy then reads its opening brace as a
    # global-options block and refuses to start the *whole* edge, app host
    # included. Measured against caddy:2-alpine, not assumed.
    assert Settings.model_fields["MARKETING_DOMAIN"].default != ""
    example = (REPO_ROOT / "backend" / ".env.example").read_text()
    assert re.search(r"^MARKETING_DOMAIN=\S+$", example, re.MULTILINE), (
        "backend/.env.example must ship a non-empty MARKETING_DOMAIN"
    )


def test_no_cookie_is_widened_to_a_parent_domain() -> None:
    # T-307's cookie rule, as a sweep rather than a comment. Marketing is a
    # different origin from the app and needs no session at all, so widening
    # a cookie to `.example.com` to make a "logged in" state visible over
    # there would hand the session to a static site for no benefit -- and
    # T-220 already lost a session to a cross-origin mistake once.
    #
    # The refresh cookie is additionally named `__Host-refresh_token`, a
    # prefix browsers refuse to accept *with* a Domain attribute, so the
    # guard rail is real and this test is the early warning.
    # Parsed rather than grepped: a `set_cookie(` call spans a dozen lines
    # here, and a regex that stopped at the first `)` would miss a `domain=`
    # written after one -- a false negative, which is the wrong direction for
    # a check like this to be wrong in.
    offenders = []
    for path in (REPO_ROOT / "backend" / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if called != "set_cookie":
                continue
            if any(keyword.arg == "domain" for keyword in node.keywords):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], (
        "these set_cookie() calls pass a domain= -- see the T-307 note in the "
        f"repo-root Caddyfile before widening a cookie: {offenders}"
    )


def test_caddyfiles_carry_the_cookie_note_where_the_next_person_will_look() -> None:
    for name in ("Caddyfile", "Caddyfile.prod"):
        content = (REPO_ROOT / name).read_text()
        assert "__Host-refresh_token" in content
        assert "Do not widen the session cookie" in content
