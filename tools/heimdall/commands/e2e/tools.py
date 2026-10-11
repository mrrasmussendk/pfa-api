"""The tools that run a scenario, one class each: where its test lives, the stub ``heimdall e2e new`` writes,
how the test names what it asserts, and the command that runs a batch of them. A test asserts each clause of
the scenario's ``then`` under the name ``then: <clause>`` — no clause unasserted, no assertion the scenario does
not state — so the text cannot drift from the test without a check failing. A stub asserts every clause and
fails on each until the agent writes it, so a scaffolded scenario cannot pass until its test is written.

- ``postman``     an HTTP request in the git-native collection ``postman/collections/e2e`` (APIs);
- ``playwright``  a ``.spec.ts`` beside the scenario, run with ``npx playwright test`` (frontends);
- ``pytest``      a ``_e2e.py`` beside the scenario (services, CLIs, anything Python can drive). The name keeps
                  it out of the unit run, which collects ``test_*.py`` only.

Every test reaches the started system through its base URL: ``{{base_url}}`` in Postman, ``BASE_URL`` for
Playwright, ``E2E_BASE_URL`` for pytest."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from typing import TYPE_CHECKING

from .base import SCENARIOS_DIR, E2EError

if TYPE_CHECKING:
    from .scenarios import Scenario

NOT_WRITTEN = "not written yet: assert the `then`"
THEN = "then: "  # the prefix of every assertion's name; the rest is the clause, word for word

# A JavaScript string literal opening a test's argument list, e.g. ``pm.test("then: 422", ...)``.
_JS_NAME = r"""\(\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')"""
# ``when`` in an API scenario names its request as ``<METHOD> /<path>``.
_REQUEST = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (/[^\s,;]*)")
# A Postman request's method and URL, as the git-native collection writes them.
_SENT_METHOD = re.compile(r"^method:\s*(\S+)", re.M)
_SENT_URL = re.compile(r"^url:\s*'?\{\{base_url\}\}([^'\s]*)", re.M)


def _path(url: str) -> str:
    """The path a request goes to: no query, no full stop a sentence ended it with, ``/`` when empty."""
    return url.split("?", 1)[0].split("#", 1)[0].rstrip(".") or "/"


# A JavaScript escape: a code point by number, or one character, mapped through ``_CONTROL`` or standing for itself.
_ESCAPE = re.compile(r"\\(?:u\{([0-9a-fA-F]{1,6})\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(.))", re.S)
_CONTROL = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unescape(m: re.Match[str]) -> str:
    code = m.group(1) or m.group(2) or m.group(3)
    return chr(int(code, 16)) if code else _CONTROL.get(m.group(4), m.group(4))


def _unquote(literal: str) -> str:
    """The text of a JavaScript string literal in either quote, its escapes decoded; a surrogate pair written as
    two ``\\uXXXX`` escapes is joined into its one character."""
    text = _ESCAPE.sub(_unescape, literal[1:-1])
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _js(name: str) -> str:
    """``name`` as a JavaScript string literal, readable whatever language it is in."""
    return json.dumps(name, ensure_ascii=False)


def clause_problems(then: tuple[str, ...], asserted: list[str]) -> list[str]:
    """What keeps ``asserted`` from being ``then``, clause for clause."""
    problems = [f"no assertion named {THEN + c!r}" for c in then if c not in asserted]
    problems += [f"asserts {THEN + a!r}, which the scenario's Then does not say" for a in asserted if a not in then]
    problems += [f"asserts {THEN + a!r} more than once" for a in sorted({a for a in asserted if asserted.count(a) > 1})]
    return problems


def _which(exe: str) -> str:
    found = shutil.which(exe)
    if found is None:
        raise E2EError(f"{exe} not found on PATH")
    return found


class Tool:
    name = ""
    methods: frozenset[str] | None = None  # the methods it can test; None for any
    assertion = re.compile("$^")  # group 1: the literal naming one assertion

    def test_path(self, sid: str) -> str:
        raise NotImplementedError

    def asserted(self, test: str) -> list[str]:
        """The clauses ``test`` asserts, from the names of its assertions that start with ``THEN``."""
        names = [_unquote(m.group(1)) for m in self.assertion.finditer(test)]
        return [n[len(THEN) :] for n in names if n.startswith(THEN)]

    def check_test(self, s: Scenario, test: str) -> list[str]:
        """What keeps ``test`` from doing what ``s`` says; empty when it asserts the ``then``, clause for clause."""
        return clause_problems(s.then, self.asserted(test))

    def stub(self, s: Scenario) -> str:
        raise NotImplementedError

    def command(self, scenarios: list[Scenario], base_url: str) -> tuple[list[str], dict[str, str]]:
        raise NotImplementedError


class Postman(Tool):
    name = "postman"
    methods = frozenset({"API"})
    assertion = re.compile(r"\bpm\.test" + _JS_NAME)
    collection = "postman/collections/e2e"

    def test_path(self, sid: str) -> str:
        return f"{self.collection}/{sid}.request.yaml"

    def check_test(self, s: Scenario, test: str) -> list[str]:
        """The ``then``, and the request: the method and path the ``when`` names are the ones the test sends."""
        problems = super().check_test(s, test)
        named = [m.groups() for w in s.when for m in _REQUEST.finditer(w)]
        if len(named) != 1:
            return [*problems, "its When must name the one request it sends, as `<METHOD> /<path>`"]
        method, path = named[0][0], _path(named[0][1])
        sent_method, sent_url = _SENT_METHOD.search(test), _SENT_URL.search(test)
        sends = (sent_method.group(1) if sent_method else None, _path(sent_url.group(1)) if sent_url else None)
        if sends != (method, path):
            problems.append(f"sends {sends[0]} {sends[1]}, but the When says {method} {path}")
        return problems

    def stub(self, s: Scenario) -> str:
        named = next(((m.group(1), m.group(2).rstrip(".")) for w in s.when for m in _REQUEST.finditer(w)), ("GET", "/"))
        tests = "".join(
            f"      pm.test({_js(THEN + c)}, () => {{\n        pm.expect.fail({_js(NOT_WRITTEN)});\n      }});\n" for c in s.then
        )
        return (
            f"$kind: http-request\nmethod: {named[0]}\nurl: '{{{{base_url}}}}{named[1]}'\norder: 1000\nscripts:\n"
            "  - type: afterResponse\n    language: text/javascript\n    code: |-\n"
            + "".join(f"      // given: {g}\n" for g in s.given)
            + "".join(f"      // when: {w}\n" for w in s.when)
            + "      // add the body: the given and when say what to send\n"
            + tests
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
    assertion = re.compile(r"(?<![\w.])test" + _JS_NAME)

    def test_path(self, sid: str) -> str:
        return f"{SCENARIOS_DIR}/{sid}.spec.ts"

    def stub(self, s: Scenario) -> str:
        return (
            'import { test, expect } from "@playwright/test";\n\n'
            + "".join(f"// given: {g}\n" for g in s.given)
            + "".join(f"// when: {w}\n" for w in s.when)
            + "".join(
                f"\ntest({_js(THEN + c)}, async ({{ page }}) => {{\n"
                '  await page.goto(process.env.BASE_URL ?? "/");\n'
                f"  expect(false, {_js(NOT_WRITTEN)}).toBe(true);\n}});\n"
                for c in s.then
            )
        )

    def command(self, scenarios: list[Scenario], base_url: str) -> tuple[list[str], dict[str, str]]:
        return [_which("npx"), "playwright", "test", *(self.test_path(s.id) for s in scenarios)], {"BASE_URL": base_url}


class Pytest(Tool):
    name = "pytest"
    # A test function whose docstring, on the line after ``def``, names the clause.
    assertion = re.compile(r"^def test_\w*\([^)]*\)[^:\n]*:\s*\n\s+(\"(?:[^\"\\\n]|\\.)*\")", re.M)

    def test_path(self, sid: str) -> str:
        return f"{SCENARIOS_DIR}/{sid}_e2e.py"

    def stub(self, s: Scenario) -> str:
        # Steps as comments, where free text cannot end early; each clause as a one-line string docstring, escaped.
        return (
            "".join(f"# given: {g}\n" for g in s.given) + "".join(f"# when: {w}\n" for w in s.when) + "\nimport os\n\n"
            'BASE_URL = os.environ.get("E2E_BASE_URL", "")\n'
            + "".join(
                f"\n\ndef test_then_{i}() -> None:\n    {_js(THEN + c)}\n    raise AssertionError({_js(NOT_WRITTEN)})\n"
                for i, c in enumerate(s.then, start=1)
            )
        )

    def command(self, scenarios: list[Scenario], base_url: str) -> tuple[list[str], dict[str, str]]:
        files = [self.test_path(s.id) for s in scenarios]
        return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files], {"E2E_BASE_URL": base_url}


TOOLS: dict[str, Tool] = {t.name: t for t in (Postman(), Playwright(), Pytest())}


def environment(extra: dict[str, str]) -> dict[str, str]:
    return {**os.environ, **{k: v for k, v in extra.items() if v}}
