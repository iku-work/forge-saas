"""
T-212: makes "every OWASP Top 10 risk has a test proving its mitigation" a
machine-checked claim instead of a table that silently drifts from the code.

`request.session.items` (populated by pytest's own collection, before any
test runs) already holds every test node for this run -- reading their
`owasp` markers here needs no subprocess re-invocation of pytest, and by the
time this test executes the "Backend tests" CI step is already running the
suite with `-x`, so a category's marked test failing anywhere else in the
run fails the whole step before this one even reports its own result.

That same property makes this test sensitive to *deselection*: a `-m` filter
removes items from `request.session.items` too. T-291's `module_dependent`
marker is the one filter in play (CI's module-matrix job runs `-m "not
module_dependent"`), and every OWASP category currently keeps at least one
unmarked test, so the claim survives there. If a category ever fails here
during that job and nowhere else, this is why: its last remaining
`@pytest.mark.owasp` test became module-dependent, and the category now needs
a proof that holds with every optional module off.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
DOC_PATH = REPO_ROOT / "docs" / "security" / "owasp-top10.md"

_ALL_CATEGORIES = {f"A{n:02d}" for n in range(1, 11)}

# Backtick-quoted paths starting with a known top-level source dir -- avoids
# matching prose or command-line flags (e.g. `--config auto`) also written
# in backticks elsewhere in the doc.
_PATH_IN_BACKTICKS = re.compile(r"`(backend/[^`]+)`")


def test_every_owasp_category_has_a_marked_test(request: pytest.FixtureRequest) -> None:
    seen: set[str] = set()
    for item in request.session.items:
        for mark in item.iter_markers(name="owasp"):
            seen.update(mark.args)

    missing = _ALL_CATEGORIES - seen
    assert not missing, f"OWASP categories with no @pytest.mark.owasp(...) test: {sorted(missing)}"

    unknown = seen - _ALL_CATEGORIES
    assert not unknown, f"@pytest.mark.owasp(...) used with unknown categories: {sorted(unknown)}"


def test_doc_references_only_existing_files() -> None:
    content = DOC_PATH.read_text(encoding="utf-8")
    referenced_paths = _PATH_IN_BACKTICKS.findall(content)

    assert referenced_paths, "expected at least one backend/... path referenced in the doc"
    missing = [path for path in referenced_paths if not (REPO_ROOT / path).is_file()]
    assert not missing, f"owasp-top10.md references paths that don't exist: {missing}"
