"""
`make check-docs` (T-252): the mechanical half of the ADR + runbook contract.

Two document types in `docs/` have a template and a lifecycle, and both decay
the same way -- not by anyone deciding to stop, but by one document quietly
not following the shape and the next one copying it. This script holds the
part of that shape a script can actually hold:

- every ADR carries a parseable status (`Proposed` / `Accepted` /
  `Superseded by ADR-NNNN`, the last pointing at a file that exists) and a
  `**Date:**`, and has its five sections in order;
- `docs/adr/README.md`'s index table is derived from those ADRs rather than
  maintained beside them. `--write` regenerates it; without `--write` a
  mismatch fails the build, which is the same guarantee from the other side;
- every runbook has the template's six headings, in order.

What it deliberately does not check is whether any of the prose is true. A
green run means the documents are shaped right, not that they are right --
the rule that keeps them true is "the ADR ships in the same PR as the change
it describes", and no script enforces that one.

A document in `docs/runbooks/` that is not a procedure -- a reference, an
inventory, the record of a verification pass that already happened -- opts out
with `<!-- runbook-template-exempt: <reason> -->` in its first few lines. The
reason is required (an empty one fails) and is read by a human: the marker
exists so the four such documents that live there today don't force a Rollback
section onto a document that has nothing to roll back, not as a way out of
writing one.

Runnable from a bare checkout: no imports from `app`, no settings, no
database. The `--docs-root` flag is what lets the tests point it at a scratch
tree instead of the real one.
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ADR_SUBDIR = "adr"
RUNBOOK_SUBDIR = "runbooks"

# The template is the shape, and README is the index -- neither is an instance
# of the thing being checked.
NOT_A_DOCUMENT = frozenset({"_template.md", "README.md"})

ADR_FILENAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
ADR_TITLE = re.compile(r"^# ADR-(\d{4}): +(\S.*)$")
ADR_STATUS = re.compile(r"^(Proposed|Accepted|Superseded by ADR-(\d{4}))\b")
ADR_DATE = re.compile(r"^\*\*Date:\*\* +(\d{4}-\d{2}-\d{2})$")

# Non-greedy up to the first `-->`: markdown comments don't nest, so the first
# terminator is the only possible one.
RUNBOOK_EXEMPT = re.compile(r"<!--\s*runbook-template-exempt:\s*(.*?)\s*-->", re.DOTALL)
RUNBOOK_EXEMPT_SEARCH_LINES = 8

H2 = 2

ADR_SECTIONS = ("Status", "Context", "Decision", "Consequences", "Alternatives considered")
RUNBOOK_SECTIONS = ("Trigger", "Pre-checks", "Steps", "Verification", "Rollback", "Escalation")

INDEX_START = "<!-- adr-index:start -->"
INDEX_END = "<!-- adr-index:end -->"
INDEX_HEADER = ("| ADR | Title | Status | Date |", "|---|---|---|---|")


@dataclass(frozen=True)
class Problem:
    """One thing to fix, addressed the way an editor can jump to it."""

    path: Path
    line: int
    message: str

    def render(self, root: Path) -> str:
        try:
            where = self.path.relative_to(root).as_posix()
        except ValueError:
            where = self.path.as_posix()
        return f"{where}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Adr:
    path: Path
    number: str
    title: str
    status: str
    supersedes_target: str | None
    day: str

    def index_row(self) -> str:
        return f"| [{self.number}]({self.path.name}) | {self.title} | {self.status} | {self.day} |"


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line: int


def headings(text: str) -> list[Heading]:
    """Every ATX heading, ignoring anything inside a fenced code block.

    The fence check is not decoration: several runbooks paste YAML and SQL
    containing `#` comments at the start of a line, and counting one of those
    as a heading would make the section-order check nonsense.
    """
    found: list[Heading] = []
    fence: str | None = None
    for number, raw in enumerate(text.split("\n"), start=1):
        stripped = raw.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue
        match = re.match(r"^(#{1,6}) +(\S.*?)\s*$", raw)
        if match:
            found.append(Heading(len(match.group(1)), match.group(2), number))
    return found


def _section_lines(text: str, heading: Heading) -> list[str]:
    """The body of one section: everything until the next heading at or above it."""
    lines = text.split("\n")
    body: list[str] = []
    for candidate in lines[heading.line :]:
        match = re.match(r"^(#{1,6}) +\S", candidate)
        if match and len(match.group(1)) <= heading.level:
            break
        body.append(candidate)
    return body


def check_section_order(
    path: Path, found: list[Heading], required: tuple[str, ...], level: int
) -> list[Problem]:
    """Required headings all present, in the given order. Extra headings are fine.

    Extras are allowed anywhere on purpose -- a runbook that needs a
    "Per-integration behavior" table between its Trigger and its Steps should
    have one, and a rule that forbade it would be a rule people work around
    rather than follow.
    """
    problems: list[Problem] = []
    at_level = [h for h in found if h.level == level]
    positions: dict[str, int] = {}
    for name in required:
        matches = [h for h in at_level if h.text.casefold() == name.casefold()]
        if not matches:
            problems.append(Problem(path, 1, f"missing required section `{'#' * level} {name}`"))
            continue
        if len(matches) > 1:
            problems.append(
                Problem(path, matches[1].line, f"duplicate section `{'#' * level} {name}`")
            )
        positions[name] = matches[0].line

    ordered = [name for name in required if name in positions]
    for earlier, later in itertools.pairwise(ordered):
        if positions[earlier] > positions[later]:
            problems.append(
                Problem(
                    path,
                    positions[later],
                    f"section `{later}` must come after `{earlier}`, not before it",
                )
            )
    return problems


def _parse_title(
    path: Path, found: list[Heading], name_match: re.Match[str] | None
) -> tuple[re.Match[str] | None, list[Problem]]:
    """`# ADR-NNNN: Title`, agreeing with the filename it lives in."""
    heading = next((h for h in found if h.level == 1), None)
    if heading is None:
        return None, [Problem(path, 1, "no `# ADR-NNNN: Title` heading")]

    match = ADR_TITLE.match(f"# {heading.text}")
    if match is None:
        return None, [
            Problem(
                path,
                heading.line,
                f"heading must read `# ADR-NNNN: Title`, not `{heading.text}`",
            )
        ]
    if name_match is not None and match.group(1) != name_match.group(1):
        return None, [
            Problem(
                path,
                heading.line,
                f"heading says ADR-{match.group(1)} but the filename says {name_match.group(1)}",
            )
        ]
    if "|" in match.group(2):
        return None, [
            Problem(
                path,
                heading.line,
                "title cannot contain `|` -- it becomes an index table cell",
            )
        ]
    return match, []


def parse_adr(path: Path) -> tuple[Adr | None, list[Problem]]:
    """Everything the index needs, plus every reason the file can't supply it."""
    problems: list[Problem] = []
    text = path.read_text(encoding="utf-8")
    found = headings(text)

    name_match = ADR_FILENAME.match(path.name)
    if name_match is None:
        problems.append(
            Problem(path, 1, "filename must be NNNN-kebab-slug.md (four digits, then a slug)")
        )

    title_match, title_problems = _parse_title(path, found, name_match)
    problems.extend(title_problems)

    problems.extend(check_section_order(path, found, ADR_SECTIONS, level=2))

    status: str | None = None
    target: str | None = None
    day: str | None = None
    status_heading = next(
        (h for h in found if h.level == H2 and h.text.casefold() == "status"), None
    )
    if status_heading is not None:
        body = _section_lines(text, status_heading)
        first = next((line for line in body if line.strip()), "")
        status_match = ADR_STATUS.match(first.strip())
        if status_match is None:
            problems.append(
                Problem(
                    path,
                    status_heading.line + 1,
                    "status must begin `Proposed`, `Accepted`, or `Superseded by ADR-NNNN` "
                    f"-- found {first.strip()!r}",
                )
            )
        else:
            status = status_match.group(1)
            target = status_match.group(2)

        dates = [ADR_DATE.match(line.strip()) for line in body]
        matched = [m for m in dates if m is not None]
        if not matched:
            problems.append(
                Problem(
                    path,
                    status_heading.line,
                    "no `**Date:** YYYY-MM-DD` line in the Status section",
                )
            )
        else:
            day = matched[0].group(1)
            try:
                date.fromisoformat(day)
            except ValueError:
                problems.append(Problem(path, status_heading.line, f"`{day}` is not a real date"))
                day = None

    if name_match is None or title_match is None or status is None or day is None:
        return None, problems

    return (
        Adr(
            path=path,
            number=name_match.group(1),
            title=title_match.group(2),
            status=status,
            supersedes_target=target,
            day=day,
        ),
        problems,
    )


def check_runbook(path: Path) -> list[Problem]:
    text = path.read_text(encoding="utf-8")

    head = "\n".join(text.split("\n")[:RUNBOOK_EXEMPT_SEARCH_LINES])
    exempt = RUNBOOK_EXEMPT.search(head)
    if exempt is not None:
        if not exempt.group(1).strip():
            return [
                Problem(
                    path,
                    1,
                    "runbook-template-exempt needs a reason, and it is read by a human",
                )
            ]
        return []
    if RUNBOOK_EXEMPT.search(text) is not None:
        return [
            Problem(
                path,
                1,
                f"runbook-template-exempt must be in the first {RUNBOOK_EXEMPT_SEARCH_LINES} "
                "lines, where a reader meets it before the content",
            )
        ]

    return check_section_order(path, headings(text), RUNBOOK_SECTIONS, level=2)


def render_index(adrs: list[Adr]) -> str:
    rows = [adr.index_row() for adr in sorted(adrs, key=lambda a: a.number)]
    return "\n".join([*INDEX_HEADER, *rows])


def _replace_index(readme_text: str, table: str) -> str:
    start = readme_text.index(INDEX_START) + len(INDEX_START)
    end = readme_text.index(INDEX_END)
    return f"{readme_text[:start]}\n{table}\n{readme_text[end:]}"


def check_index(readme: Path, adrs: list[Adr], write: bool) -> list[Problem]:
    """The index is derived, never maintained: either it matches or `--write` fixes it."""
    if not readme.exists():
        return [Problem(readme, 1, "the ADR index is missing")]

    text = readme.read_text(encoding="utf-8")
    if INDEX_START not in text or INDEX_END not in text:
        return [Problem(readme, 1, f"no `{INDEX_START}` / `{INDEX_END}` markers to generate into")]
    if text.index(INDEX_END) < text.index(INDEX_START):
        return [Problem(readme, 1, f"`{INDEX_END}` appears before `{INDEX_START}`")]

    table = render_index(adrs)
    current = text[text.index(INDEX_START) + len(INDEX_START) : text.index(INDEX_END)].strip("\n")
    if current == table:
        return []

    if write:
        readme.write_text(_replace_index(text, table), encoding="utf-8")
        return []

    line = text[: text.index(INDEX_START)].count("\n") + 1
    return [
        Problem(
            readme,
            line,
            "index does not match the ADRs on disk -- run `make check-docs ARGS=--write`",
        )
    ]


def check(docs_root: Path, write: bool = False) -> list[Problem]:
    problems: list[Problem] = []

    adr_dir = docs_root / ADR_SUBDIR
    adrs: list[Adr] = []
    for path in sorted(adr_dir.glob("*.md")):
        if path.name in NOT_A_DOCUMENT:
            continue
        adr, found = parse_adr(path)
        problems.extend(found)
        if adr is not None:
            adrs.append(adr)

    numbers = {adr.number for adr in adrs}
    problems.extend(
        Problem(adr.path, 1, f"superseded by ADR-{adr.supersedes_target}, which does not exist")
        for adr in adrs
        if adr.supersedes_target is not None and adr.supersedes_target not in numbers
    )
    for number, group in _by_number(adrs).items():
        if len(group) > 1:
            names = ", ".join(sorted(p.path.name for p in group))
            problems.append(
                Problem(group[1].path, 1, f"ADR number {number} is used twice: {names}")
            )

    problems.extend(check_index(adr_dir / "README.md", adrs, write))

    for path in sorted((docs_root / RUNBOOK_SUBDIR).glob("*.md")):
        if path.name in NOT_A_DOCUMENT:
            continue
        problems.extend(check_runbook(path))

    return problems


def _by_number(adrs: list[Adr]) -> dict[str, list[Adr]]:
    grouped: dict[str, list[Adr]] = {}
    for adr in adrs:
        grouped.setdefault(adr.number, []).append(adr)
    return grouped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate docs/adr/README.md's index instead of failing on a mismatch",
    )
    parser.add_argument(
        "--docs-root",
        type=Path,
        default=REPO_ROOT / "docs",
        help="the docs/ directory to check (default: this repo's)",
    )
    args = parser.parse_args(argv)

    docs_root: Path = args.docs_root
    if not (docs_root / ADR_SUBDIR).is_dir():
        print(f"no ADR directory at {(docs_root / ADR_SUBDIR)}", file=sys.stderr)
        return 2

    problems = check(docs_root, write=bool(args.write))
    if problems:
        print("docs check failed:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem.render(docs_root.parent)}", file=sys.stderr)
        print(
            "\nADR shape: docs/adr/_template.md. Runbook shape: docs/runbooks/_template.md.",
            file=sys.stderr,
        )
        return 1

    print("docs check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
