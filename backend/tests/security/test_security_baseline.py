"""
T-213: the ASVS L2 checklist (docs/adr/0001-security-baseline.md) links
evidence -- code, tests, config -- for every `met`/`partial` row. This test
is the same "doc references only real files" check T-212 already applies
to docs/security/owasp-top10.md (backend/tests/security/test_owasp_mapping.py),
scoped to the wider set of path-shaped references this document makes
(backend/..., .github/..., docs/..., and known root files like Dockerfile).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent
DOC_PATH = REPO_ROOT / "docs" / "adr" / "0001-security-baseline.md"

_BACKTICKED = re.compile(r"`([A-Za-z0-9_.\-/]+)`")
_KNOWN_ROOT_FILES = {"Caddyfile", "Caddyfile.prod", "Dockerfile"}
_REPO_PATH_PREFIXES = ("backend/", ".github/", "docs/")


def _looks_like_repo_path(candidate: str) -> bool:
    return candidate.startswith(_REPO_PATH_PREFIXES) or candidate in _KNOWN_ROOT_FILES


def test_doc_references_only_existing_paths() -> None:
    content = DOC_PATH.read_text(encoding="utf-8")
    candidates = {m for m in _BACKTICKED.findall(content) if _looks_like_repo_path(m)}

    assert candidates, "expected at least one repo-relative path referenced in the doc"

    missing = [path for path in candidates if not (REPO_ROOT / path).exists()]
    assert not missing, f"0001-security-baseline.md references paths that don't exist: {missing}"


def test_every_row_has_a_non_empty_status() -> None:
    content = DOC_PATH.read_text(encoding="utf-8")
    status_cells = re.findall(
        r"^\| V\d+\.[\d.]+ \| .+ \| (met|partial|n/a) \| .+ \|$",
        content,
        re.MULTILINE,
    )
    row_count = len(re.findall(r"^\| V\d+\.[\d.]+ \| ", content, re.MULTILINE))

    assert row_count > 0, "expected at least one ASVS requirement row in the doc"
    assert len(status_cells) == row_count, (
        f"found {row_count} requirement rows but only {len(status_cells)} had a "
        "recognized status (met/partial/n/a) in the expected column"
    )
