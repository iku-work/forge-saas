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
    assert "redir https://localhost{uri} 301" in content
