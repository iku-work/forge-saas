import ast
import base64
import hashlib
import re
from pathlib import Path

import pytest
import yaml

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


def test_the_marketing_block_reaches_caddy_by_both_supported_routes() -> None:
    """Marketing's site block and its dist have to arrive somehow, and since
    2026-09-14 there are two ways.

    The mounts moved out of docker-compose.prod.yml because compose merges
    `volumes` by appending: an overlay cannot remove a base mount, so a
    leftover bind of ./apps/marketing/dist would shadow the copy baked into
    the edge image. Both routes are asserted here rather than only the one
    this deployment happens to use -- the other is exactly the sort of thing
    that rots unnoticed.
    """
    host_build = _compose("docker-compose.host-build.yml")
    assert "- ./apps/marketing/dist:/srv/marketing:ro" in host_build
    assert "- ./apps/marketing/caddy/prod:/etc/caddy/conf.d:ro" in host_build

    # The registry route bakes the same two paths into the image instead.
    dockerfile = (REPO_ROOT / "Dockerfile.caddy").read_text(encoding="utf-8")
    assert "/repo/apps/marketing/dist /srv/marketing" in dockerfile
    assert "/repo/apps/marketing/caddy/prod /etc/caddy/conf.d" in dockerfile

    # And the base file must carry neither, or it would shadow them.
    base = _compose("docker-compose.prod.yml")
    assert "/srv/marketing" not in base
    assert "/etc/caddy/conf.d" not in base


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


def test_storage_host_is_declared_and_never_blank() -> None:
    """MinIO on the same box needs a public hostname, not a bridge address.

    There is one AWS_ENDPOINT_URL, and the presigned URLs built from it go to
    the browser -- a SigV4 download signature covers the Host header, so the
    browser cannot be pointed somewhere else afterwards. Same never-blank rule
    as MARKETING_DOMAIN: Caddy substitutes a set-but-empty variable as the
    empty string rather than taking the `{$VAR:default}` fallback, and a site
    block with no address takes the whole edge down.
    """
    content = (REPO_ROOT / "Caddyfile.prod").read_text()

    assert "{$STORAGE_DOMAIN:storage.localhost}" in content
    assert "reverse_proxy minio:9000" in content
    # The console is not a public surface, whatever else is. Checked against
    # the directives rather than the whole file, which also mentions 9001 in
    # prose explaining why it is absent.
    directives = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("9001" in line for line in directives)


def test_prod_compose_keeps_minio_off_the_public_interface() -> None:
    """Caddy is the only way in. A stray `ports:` here would publish the
    object store, and its console, straight onto the internet."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.prod.yml").read_text())

    assert "ports" not in compose["services"]["minio"]
    assert compose["services"]["minio"]["env_file"] == ["./.env.minio"]


# --- the app origin's document headers -------------------------------------------------------
#
# PROD-03: the SPA's HTML is served by Caddy from /srv/web, so
# app.middleware.security_headers never sees it -- that middleware protects
# API responses. This block used to set HSTS and nothing else, leaving the
# one origin that renders untrusted content into a DOM with no framing,
# sniffing, referrer or permissions policy, and no CSP.


def _app_site_block() -> str:
    """The `{$DOMAIN} { ... }` block, without the marketing or storage ones.

    Sliced rather than parsed: the assertions below are about headers being
    present on *this* host, and the neighbouring blocks legitimately carry
    their own copies (marketing's are generated by
    apps/marketing/scripts/caddy-site.mjs), so a whole-file grep would pass
    on the wrong site's headers.
    """
    content = (REPO_ROOT / "Caddyfile.prod").read_text(encoding="utf-8")
    start = content.index("{$DOMAIN} {")
    end = content.index("\nhttp://{$DOMAIN}", start)
    return content[start:end]


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("Strict-Transport-Security", "max-age=31536000"),
        ("X-Frame-Options", "DENY"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Permissions-Policy", "camera=()"),
        ("Content-Security-Policy", "default-src 'self'"),
    ],
)
def test_the_app_origin_sets_its_document_headers(header: str, value: str) -> None:
    block = _app_site_block()

    assert header in block, f"{header} missing from the {{$DOMAIN}} site block"
    line = next(candidate for candidate in block.splitlines() if header in candidate)
    assert value in line


def _inline_script_hashes(html: str) -> list[str]:
    """Every inline script's CSP source expression, as a browser computes it.

    The browser hashes the element's exact text content, so this hashes the
    bytes between the tags with no normalisation of any kind -- which is why
    .gitattributes pins this file to LF.
    """
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
        for body in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    ]


def test_the_csp_carries_the_hash_of_the_inline_theme_script() -> None:
    """Keeps one constant in step with the file it describes.

    apps/web/index.html's theme-boot script has to run synchronously before
    first paint, so it cannot become an external file that `'self'` would
    cover. Editing it changes its hash, and a stale hash in the CSP means the
    script is blocked -- which shows up as a white flash into a dark app, not
    as an error anyone would connect back to this policy.
    """
    html = (REPO_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    hashes = _inline_script_hashes(html)
    assert hashes, "expected an inline script in index.html; did it move?"

    csp = next(line for line in _app_site_block().splitlines() if "Content-Security-Policy" in line)
    for digest in hashes:
        assert digest in csp, (
            f"index.html's inline script hashes to {digest}, which is not in the CSP. "
            f"Update the script-src in Caddyfile.prod."
        )


def test_index_html_stays_lf_so_that_hash_is_machine_independent() -> None:
    # A browser hashes the bytes it is served. Built on Linux those are LF;
    # checked out on Windows without the .gitattributes rule they are CRLF,
    # and the committed hash would be correct on exactly one of the two.
    raw = (REPO_ROOT / "apps" / "web" / "index.html").read_bytes()

    assert b"\r\n" not in raw, "apps/web/index.html must stay LF -- see .gitattributes"


def test_the_app_csp_keeps_script_src_strict() -> None:
    # The point of the whole policy. 'unsafe-inline' here would readmit the
    # injected-script attack it exists to stop -- and is a tempting fix for
    # the hash test above failing.
    csp = next(line for line in _app_site_block().splitlines() if "Content-Security-Policy" in line)
    script_src = csp.split("script-src")[1].split(";")[0]

    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src


def test_caddy_gets_no_application_secrets() -> None:
    """PROD-02: the edge container's environment is an allowlist.

    It used to be `env_file: ./backend/.env` -- the whole application
    environment, including JWT_PRIVATE_KEY, ENCRYPTION_KEY, DATABASE_URL and
    STRIPE_SECRET_KEY, handed to the one container the internet can reach.
    Caddy needs four hostnames and a CIDR list.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    caddy = compose["services"]["caddy"]

    assert "env_file" not in caddy, (
        "the caddy service must not take an env_file -- it cannot select keys, "
        "so every application secret goes into the edge process with it"
    )
    assert set(caddy["environment"]) == {
        "DOMAIN",
        "MARKETING_DOMAIN",
        "STORAGE_DOMAIN",
        "CADDY_TRUSTED_PROXIES",
    }


def test_every_variable_the_caddyfiles_use_is_supplied_to_caddy() -> None:
    # The other half of the allowlist: narrowing the environment is only safe
    # if nothing the config actually reads was narrowed away. A missing one
    # does not fail loudly -- Caddy substitutes its `{$VAR:default}` fallback,
    # or an empty string when there is no default.
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    supplied = set(compose["services"]["caddy"]["environment"])

    content = (REPO_ROOT / "Caddyfile.prod").read_text(encoding="utf-8")
    used = set(re.findall(r"\{\$([A-Z_][A-Z0-9_]*)", content))

    assert used <= supplied, (
        f"Caddyfile.prod reads {sorted(used - supplied)}, which compose does not pass"
    )


def _dev_app_site_block() -> str:
    content = (REPO_ROOT / "Caddyfile").read_text(encoding="utf-8")
    start = content.index("localhost, caddy {")
    return content[start:]


@pytest.mark.parametrize(
    "header",
    [
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
    ],
)
def test_the_dev_edge_sets_the_same_document_headers(header: str) -> None:
    """Parity is what makes the policy testable before it reaches users.

    The Playwright suite and every `make dev` session run against this file.
    A CSP that exists only in Caddyfile.prod is first exercised in
    production, by customers, and the failure mode is a blank page.
    """
    assert header in _dev_app_site_block()


def test_the_two_edges_agree_on_the_script_hash() -> None:
    # The one part of the policy that is a *fact about a file* rather than a
    # choice. If dev and prod disagree here, one of them is stale.
    def script_src(block: str) -> str:
        line = next(ln for ln in block.splitlines() if "Content-Security-Policy" in ln)
        return line.split("script-src")[1].split(";")[0].strip()

    assert script_src(_dev_app_site_block()) == script_src(_app_site_block())
