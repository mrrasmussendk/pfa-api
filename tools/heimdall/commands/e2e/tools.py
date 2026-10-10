"""The tools that run a scenario, one class each: where its test lives, the stub ``heimdall e2e new`` writes,
how to tell the test asserts something, and the command that runs a batch of them. A stub always fails — it
says which ``then`` is not asserted yet — so a scaffolded scenario cannot pass until the agent writes the test.

- ``postman``     an HTTP request in the git-native collection ``postman/collections/e2e`` (APIs);
- ``playwright``  a ``.spec.ts`` beside the scenario, run with ``npx playwright test`` (frontends);
- ``pytest``      a ``_e2e.py`` beside the scenario (services, CLIs, anything Python can drive). The name keeps
                  it out of the unit run, which collects ``test_*.py`` only.

Every test reaches the started system through its base URL: ``{{base_url}}`` in Postman, ``BASE_URL`` for
Playwright, ``E2E_BASE_URL`` for pytest."""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import TYPE_CHECKING

from .base import SCENARIOS_DIR, E2EError

if TYPE_CHECKING:
    from .scenarios import Scenario

NOT_WRITTEN = "not written yet: assert the `then`"


def _which(exe: str) -> str:
    found = shutil.which(exe)
    if found is None:
        raise E2EError(f"{exe} not found on PATH")
    return found


class Tool:
    name = ""
    methods: frozenset[str] | None = None  # the methods it can test; None for any
    marker = ""  # a test file without it asserts nothing

    def test_path(self, sid: str) -> str:
        raise NotImplementedError

    def stub(self, s: Scenario) -> str:
        raise NotImplementedError

    def command(self, scenarios: list[Scenario], base_url: str) -> tuple[list[str], dict[str, str]]:
        raise NotImplementedError


class Postman(Tool):
    name = "postman"
    methods = frozenset({"API"})
    marker = "pm.test("
    collection = "postman/collections/e2e"

    def test_path(self, sid: str) -> str:
        return f"{self.collection}/{sid}.request.yaml"

    def stub(self, s: Scenario) -> str:
        return (
            "$kind: http-request\nmethod: GET\nurl: '{{base_url}}/'\norder: 1000\nscripts:\n"
            "  - type: afterResponse\n    language: text/javascript\n    code: |-\n"
            f"      // given: {s.given}\n      // when: {s.when} (set method, url and body above to send it)\n"
            f"      pm.test({json.dumps('then: ' + s.then)}, () => {{\n"
            f"        pm.expect.fail({json.dumps(NOT_WRITTEN)});\n      }});\n"
        )

    def command(self, scenarios: list[Scenario], base_url: str) -> tuple[list[str], dict[str, str]]:
        items = [arg for s in scenarios for arg in ("-i", s.id)]
        # --no-report-events: once logged in, the CLI reports runs by default through a v2 conversion that cannot
        # find `<feature>/<name>` items, and a Stop hook has no business uploading a run report on every stop.
        argv = [_which("postman"), "collection", "run", self.collection, *items, "--disable-unicode", "--no-report-events"]
        return argv + (["--env-var", f"base_url={base_url}"] if base_url else []), {}


class Playwright(Tool):
    name = "playwright"
    methods = frozenset({"UI", "API"})
    marker = "test("

    def test_path(self, sid: str) -> str:
        return f"{SCENARIOS_DIR}/{sid}.spec.ts"

    def stub(self, s: Scenario) -> str:
        return (
            'import { test, expect } from "@playwright/test";\n\n'
            f"// given: {s.given}\n// when: {s.when}\n"
            f"test({json.dumps('then: ' + s.then)}, async ({{ page }}) => {{\n"
            '  await page.goto(process.env.BASE_URL ?? "/");\n'
            f"  expect(false, {json.dumps(NOT_WRITTEN)}).toBe(true);\n}});\n"
        )

    def command(self, scenarios: list[Scenario], base_url: str) -> tuple[list[str], dict[str, str]]:
        return [_which("npx"), "playwright", "test", *(self.test_path(s.id) for s in scenarios)], {"BASE_URL": base_url}


class Pytest(Tool):
    name = "pytest"
    marker = "def test_"

    def test_path(self, sid: str) -> str:
        return f"{SCENARIOS_DIR}/{sid}_e2e.py"

    def stub(self, s: Scenario) -> str:
        # Comments, not a docstring: a step is one line of free text, and a comment is the one place it cannot end early.
        return (
            f"# given: {s.given}\n# when: {s.when}\n# then: {s.then}\n\n"
            "import os\n\n"
            'BASE_URL = os.environ.get("E2E_BASE_URL", "")\n\n\n'
            "def test_then() -> None:\n"
            f"    raise AssertionError({json.dumps(NOT_WRITTEN)})\n"
        )

    def command(self, scenarios: list[Scenario], base_url: str) -> tuple[list[str], dict[str, str]]:
        files = [self.test_path(s.id) for s in scenarios]
        return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files], {"E2E_BASE_URL": base_url}


TOOLS: dict[str, Tool] = {t.name: t for t in (Postman(), Playwright(), Pytest())}


def environment(extra: dict[str, str]) -> dict[str, str]:
    return {**os.environ, **{k: v for k, v in extra.items() if v}}
