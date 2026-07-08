from pathlib import Path

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


def test_caddyfile_prod_blocks_public_metrics_access() -> None:
    content = (REPO_ROOT / "Caddyfile.prod").read_text()
    assert "respond /metrics 404" in content


def test_caddyfile_dev_blocks_public_metrics_access() -> None:
    content = (REPO_ROOT / "Caddyfile").read_text()
    assert "respond /metrics 404" in content
