"""
T-252: the ADR/runbook shape check, and the shapes themselves.

Two kinds of test here, and they fail for different reasons. The scratch-tree
tests (a `docs/` built in `tmp_path` holding only what the checker reads) pin
the rules: a statusless ADR fails, a drifted index fails, a runbook missing
Rollback fails. `test_the_real_docs_tree_passes` runs the same checker against
this repo's actual `docs/`, and it is the one that fails when somebody writes
a real document -- it is the whole reason the checker exists, and everything
else here is scaffolding that keeps its failures honest.

The templates are checked too, from the other direction: `_template.md` is
excluded from the sweep (it is the shape, not an instance of it), so a test
asserts each template would pass the rules it documents. A template that
drifted from its own checker would be worse than no template.
"""

from pathlib import Path

import pytest

from scripts.check_docs import (
    ADR_SECTIONS,
    RUNBOOK_SECTIONS,
    check,
    headings,
    main,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_README = """# Architecture Decision Records

<!-- adr-index:start -->
| ADR | Title | Status | Date |
|---|---|---|---|
<!-- adr-index:end -->
"""


def _adr(
    *,
    number: str = "0001",
    title: str = "A decision",
    status: str = "Accepted",
    day: str | None = "2026-01-01",
    sections: tuple[str, ...] = ADR_SECTIONS,
) -> str:
    lines = [f"# ADR-{number}: {title}", ""]
    for name in sections:
        lines += [f"## {name}", ""]
        if name == "Status":
            lines += [status, ""]
            if day is not None:
                lines += [f"**Date:** {day}", ""]
        else:
            lines += [f"Prose for {name.lower()}.", ""]
    return "\n".join(lines)


def _runbook(
    *, title: str = "Something runbook", sections: tuple[str, ...] = RUNBOOK_SECTIONS
) -> str:
    lines = [f"# {title}", "", "What this is.", ""]
    for name in sections:
        lines += [f"## {name}", "", f"Prose for {name.lower()}.", ""]
    return "\n".join(lines)


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    """A minimal, passing docs/ tree: one ADR, one runbook, a matching index."""
    root = tmp_path / "docs"
    (root / "adr").mkdir(parents=True)
    (root / "runbooks").mkdir(parents=True)
    (root / "adr" / "README.md").write_text(_README, encoding="utf-8")
    (root / "adr" / "0001-a-decision.md").write_text(_adr(), encoding="utf-8")
    (root / "runbooks" / "something.md").write_text(_runbook(), encoding="utf-8")
    check(root, write=True)
    return root


def _messages(root: Path) -> list[str]:
    return [problem.message for problem in check(root)]


def test_a_minimal_tree_passes(docs: Path) -> None:
    assert check(docs) == []


# --- the rules -------------------------------------------------------------


def test_statusless_adr_fails(docs: Path) -> None:
    """The check T-252 names explicitly: an ADR with no status doesn't ship."""
    (docs / "adr" / "0001-a-decision.md").write_text(
        _adr(sections=tuple(s for s in ADR_SECTIONS if s != "Status")), encoding="utf-8"
    )
    assert any("missing required section `## Status`" in m for m in _messages(docs))


@pytest.mark.parametrize(
    "status",
    ["Done", "accepted-ish", "WIP", "Superseded", "Superseded by 0002"],
)
def test_unparseable_status_fails(docs: Path, status: str) -> None:
    (docs / "adr" / "0001-a-decision.md").write_text(_adr(status=status), encoding="utf-8")
    assert any("status must begin" in m for m in _messages(docs))


@pytest.mark.parametrize("status", ["Proposed", "Accepted", "Accepted -- with a caveat"])
def test_the_three_allowed_statuses_pass(docs: Path, status: str) -> None:
    (docs / "adr" / "0001-a-decision.md").write_text(_adr(status=status), encoding="utf-8")
    check(docs, write=True)
    assert check(docs) == []


def test_superseded_by_a_real_adr_passes(docs: Path) -> None:
    (docs / "adr" / "0002-newer.md").write_text(
        _adr(number="0002", title="Newer"), encoding="utf-8"
    )
    (docs / "adr" / "0001-a-decision.md").write_text(
        _adr(status="Superseded by ADR-0002"), encoding="utf-8"
    )
    check(docs, write=True)
    assert check(docs) == []


def test_superseded_by_a_missing_adr_fails(docs: Path) -> None:
    (docs / "adr" / "0001-a-decision.md").write_text(
        _adr(status="Superseded by ADR-0009"), encoding="utf-8"
    )
    assert any("ADR-0009, which does not exist" in m for m in _messages(docs))


def test_missing_date_fails(docs: Path) -> None:
    (docs / "adr" / "0001-a-decision.md").write_text(_adr(day=None), encoding="utf-8")
    assert any("no `**Date:** YYYY-MM-DD` line" in m for m in _messages(docs))


def test_impossible_date_fails(docs: Path) -> None:
    """`2026-02-30` matches the digit pattern and is not a day."""
    (docs / "adr" / "0001-a-decision.md").write_text(_adr(day="2026-02-30"), encoding="utf-8")
    assert any("is not a real date" in m for m in _messages(docs))


def test_number_in_heading_must_match_the_filename(docs: Path) -> None:
    (docs / "adr" / "0001-a-decision.md").write_text(_adr(number="0007"), encoding="utf-8")
    assert any("heading says ADR-0007 but the filename says 0001" in m for m in _messages(docs))


def test_two_adrs_cannot_share_a_number(docs: Path) -> None:
    (docs / "adr" / "0001-another-decision.md").write_text(
        _adr(title="Another decision"), encoding="utf-8"
    )
    assert any("number 0001 is used twice" in m for m in _messages(docs))


def test_out_of_order_sections_fail(docs: Path) -> None:
    swapped = ("Status", "Decision", "Context", "Consequences", "Alternatives considered")
    (docs / "adr" / "0001-a-decision.md").write_text(_adr(sections=swapped), encoding="utf-8")
    assert any("`Decision` must come after `Context`" in m for m in _messages(docs))


def test_extra_sections_are_allowed_anywhere(docs: Path) -> None:
    """A document may have more to say than the template's minimum."""
    text = _adr().replace("## Consequences", "## Measured overhead\n\nNumbers.\n\n## Consequences")
    (docs / "adr" / "0001-a-decision.md").write_text(text, encoding="utf-8")
    assert check(docs) == []


# --- the index -------------------------------------------------------------


def test_index_drift_fails_and_write_fixes_it(docs: Path) -> None:
    (docs / "adr" / "0002-second.md").write_text(
        _adr(number="0002", title="Second", day="2026-02-02"), encoding="utf-8"
    )
    assert any("index does not match the ADRs on disk" in m for m in _messages(docs))

    assert check(docs, write=True) == []
    index = (docs / "adr" / "README.md").read_text(encoding="utf-8")
    assert "| [0002](0002-second.md) | Second | Accepted | 2026-02-02 |" in index
    assert check(docs) == []


def test_index_rows_are_sorted_by_number(docs: Path) -> None:
    for number, slug in [("0003", "third"), ("0002", "second")]:
        (docs / "adr" / f"{number}-{slug}.md").write_text(
            _adr(number=number, title=slug.title()), encoding="utf-8"
        )
    check(docs, write=True)
    index = (docs / "adr" / "README.md").read_text(encoding="utf-8")
    assert index.index("[0001]") < index.index("[0002]") < index.index("[0003]")


def test_index_without_markers_fails(docs: Path) -> None:
    (docs / "adr" / "README.md").write_text("# ADRs\n\nNo markers here.\n", encoding="utf-8")
    assert any("markers to generate into" in m for m in _messages(docs))


def test_write_leaves_the_rest_of_the_readme_alone(docs: Path) -> None:
    readme = docs / "adr" / "README.md"
    readme.write_text(
        _README.replace("<!-- adr-index:start -->", "Prose above.\n\n<!-- adr-index:start -->")
        + "\nProse below.\n",
        encoding="utf-8",
    )
    check(docs, write=True)
    text = readme.read_text(encoding="utf-8")
    assert "Prose above." in text
    assert "Prose below." in text


# --- runbooks --------------------------------------------------------------


@pytest.mark.parametrize("missing", RUNBOOK_SECTIONS)
def test_a_runbook_missing_any_of_the_six_fails(docs: Path, missing: str) -> None:
    (docs / "runbooks" / "something.md").write_text(
        _runbook(sections=tuple(s for s in RUNBOOK_SECTIONS if s != missing)), encoding="utf-8"
    )
    assert any(f"missing required section `## {missing}`" in m for m in _messages(docs))


def test_exempt_runbook_needs_none_of_them(docs: Path) -> None:
    (docs / "runbooks" / "something.md").write_text(
        "# A reference\n\n<!-- runbook-template-exempt: an inventory, not a procedure -->\n\n"
        "## Whatever\n",
        encoding="utf-8",
    )
    assert check(docs) == []


def test_exemption_without_a_reason_fails(docs: Path) -> None:
    (docs / "runbooks" / "something.md").write_text(
        "# A reference\n\n<!-- runbook-template-exempt: -->\n\n## Whatever\n", encoding="utf-8"
    )
    assert any("needs a reason" in m for m in _messages(docs))


def test_exemption_buried_below_the_fold_fails(docs: Path) -> None:
    """A reader has to meet the exemption before the content, not after it."""
    body = "\n".join(["# A reference", "", *["Filler." for _ in range(12)]])
    (docs / "runbooks" / "something.md").write_text(
        f"{body}\n\n<!-- runbook-template-exempt: hidden away -->\n", encoding="utf-8"
    )
    assert any("must be in the first" in m for m in _messages(docs))


def test_headings_inside_code_fences_do_not_count(docs: Path) -> None:
    """Several real runbooks paste SQL and YAML whose `#` comments start a line."""
    text = _runbook().replace(
        "## Steps\n",
        "## Steps\n\n```yaml\n# Verification\n# Rollback\n```\n",
    )
    (docs / "runbooks" / "something.md").write_text(text, encoding="utf-8")
    assert check(docs) == []

    fenced = "# Real title\n\n```\n## Trigger\n## Pre-checks\n```\n"
    assert [h.text for h in headings(fenced)] == ["Real title"]


# --- the templates and the real tree ---------------------------------------


def test_templates_are_not_treated_as_documents(docs: Path) -> None:
    """`_template.md` is the shape, so it is swept out of the checks it defines."""
    (docs / "adr" / "_template.md").write_text("# not an adr at all\n", encoding="utf-8")
    (docs / "runbooks" / "_template.md").write_text("# nor a runbook\n", encoding="utf-8")
    assert check(docs) == []


def test_each_template_would_pass_its_own_rules(tmp_path: Path) -> None:
    """The template has to be a document that passes, not just one that reads well."""
    root = tmp_path / "docs"
    (root / "adr").mkdir(parents=True)
    (root / "runbooks").mkdir(parents=True)
    (root / "adr" / "README.md").write_text(_README, encoding="utf-8")

    adr_template = (_REPO_ROOT / "docs" / "adr" / "_template.md").read_text(encoding="utf-8")
    (root / "adr" / "0001-the-template.md").write_text(
        adr_template.replace("# ADR-NNNN:", "# ADR-0001:").replace(
            "**Date:** YYYY-MM-DD", "**Date:** 2026-01-01"
        ),
        encoding="utf-8",
    )
    runbook_template = (_REPO_ROOT / "docs" / "runbooks" / "_template.md").read_text(
        encoding="utf-8"
    )
    (root / "runbooks" / "the-template.md").write_text(runbook_template, encoding="utf-8")

    check(root, write=True)
    assert check(root) == []


def test_the_real_docs_tree_passes() -> None:
    """The one that fails when a real ADR or runbook drifts."""
    assert [p.render(_REPO_ROOT) for p in check(_REPO_ROOT / "docs")] == []


def test_main_exits_zero_on_the_real_tree(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "passed" in capsys.readouterr().out


def test_main_reports_every_problem_and_points_at_the_templates(
    docs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (docs / "adr" / "0001-a-decision.md").write_text(_adr(day=None), encoding="utf-8")
    (docs / "runbooks" / "something.md").write_text(
        _runbook(sections=("Trigger",)), encoding="utf-8"
    )

    assert main(["--docs-root", str(docs)]) == 1
    err = capsys.readouterr().err
    assert "docs/adr/0001-a-decision.md:" in err
    assert "docs/runbooks/something.md:" in err
    assert "_template.md" in err


def test_main_write_repairs_a_drifted_index(docs: Path) -> None:
    (docs / "adr" / "0002-second.md").write_text(
        _adr(number="0002", title="Second"), encoding="utf-8"
    )
    assert main(["--docs-root", str(docs)]) == 1
    assert main(["--docs-root", str(docs), "--write"]) == 0
    assert main(["--docs-root", str(docs)]) == 0


def test_missing_docs_root_is_a_usage_error_not_a_pass(tmp_path: Path) -> None:
    assert main(["--docs-root", str(tmp_path / "nope")]) == 2
