"""Scaffolds a new Eitri rule: doc page, changelog line, and the four-step checklist.

Usage:  python tools/new_rule.py EIT005 "Short rule title"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not re.fullmatch(r"EIT\d{3}", argv[0]):
        print(__doc__.strip())
        return 2
    rule_id, title = argv
    core = TOOLS / "eitri" / "core.py"
    if rule_id in core.read_text(encoding="utf-8"):
        print(f"{rule_id} exists")
        return 1

    doc = REPO / "harness" / "rules" / f"{rule_id}.md"
    doc.write_text(f"# {rule_id} — {title}\nTODO: rationale + fix guidance.\n", encoding="utf-8")

    changelog = REPO / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    line = f"- {rule_id} {title}\n"
    if "## Unreleased" in text:
        text = text.replace("## Unreleased\n", "## Unreleased\n" + line, 1)
    else:
        text = text.replace("# Changelog\n", "# Changelog\n\n## Unreleased\n" + line, 1)
    changelog.write_text(text, encoding="utf-8")

    print(
        f"scaffolded harness/rules/{rule_id}.md and a changelog line. Now:\n"
        f"  1. add descriptor '{rule_id}' in tools/eitri/core.py (and ALL_DESCRIPTORS)\n"
        f"  2. implement it in tools/eitri/walls.py (or a new module wired into analyzer.py)\n"
        f"  3. add a unit test in tests/tooling/test_rules.py\n"
        f"  4. add a Brokkr canary in tools/brokkr_canary.py — the rule must provably bite"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
