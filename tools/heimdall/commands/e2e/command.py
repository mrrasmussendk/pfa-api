"""The entry point. ``heimdall e2e``: arguments → the change → the scenarios → the plan (Jev, or the slice
map) → the runner → exit code; a green run is remembered by the change's fingerprint, so a stop with nothing
new runs nothing. ``heimdall e2e --all``: every scenario, no change and no judge — what CI runs.
``heimdall e2e new``: scaffold a scenario and the failing tests that will run it. Whenever a scenario's text or
test changes, Jev is also asked whether the two still agree."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, TextIO

from ...model import MapModel
from ...store import UnreadableMap, heimdall_dir, load_map
from ..review import FileChange, ReviewError, Streams, Transport, build_state, http_transport, parse_unified_diff
from ..review.state import is_application
from ..review.transport import API_KEY_ENV, API_URL, API_URL_ENV, DEFAULT_MODEL
from .base import EXIT_ERROR, EXIT_FAILED, EXIT_NEEDS_SCENARIOS, EXIT_OK, GUIDE, SCENARIOS_DIR, SYSTEM, E2EError
from .change import Change, fingerprint, working_diff
from .judge import Plan, T, decide, drifted, fallback, own_files_changed, questions
from .runner import Runner, system_runner
from .scaffold import NewScenario, Steps, new_advice, scaffold
from .scenarios import Scenario, load_scenarios
from .system import System, load_system

USAGE = (
    "usage: heimdall e2e [--base <ref> | --diff <file>] [--task <text>] [--force]\n"
    "       heimdall e2e --all\n"
    "       heimdall e2e new <feature>/<name> --method <method> --given <text> --when <text> --then <text> [--tool <tool>]\n"
    "  repeat --given, --when or --then for each further clause: it becomes an `And`, and each `then` clause one assertion\n"
    "  default change: uncommitted and untracked files; --base <ref> adds what is committed since <ref>\n"
    "  --force runs even when the change is the one the last green run saw\n"
    "  --all runs every scenario, whatever changed, without asking Jev (what CI runs)\n"
    "  new writes the scenario and a failing test per tool; the method's tool comes from " + SYSTEM
)
MEMORY = "e2e.json"  # in .heimdall/: the fingerprint of the last change every chosen scenario passed on


class Seams(NamedTuple):
    """What a test replaces: the judge, the runner and the environment the key is read from."""

    transport: Transport | None = None
    runner: Runner | None = None
    env: Mapping[str, str] | None = None


def _options(args: list[str], flags: tuple[str, ...], values: tuple[str, ...], repeats: tuple[str, ...] = ()) -> dict[str, Any]:
    """``--flag`` → True, ``--key value`` → value and ``--repeat value`` → every value given, by name without the
    dashes; anything else is a usage error."""
    opts: dict[str, Any] = {}
    i = 0
    while i < len(args):
        a, nxt = args[i], (args[i + 1] if i + 1 < len(args) else None)
        if a[2:] in flags and a.startswith("--"):
            opts[a[2:]] = True
            i += 1
        elif a[2:] in repeats and a.startswith("--") and nxt is not None:
            opts.setdefault(a[2:], []).append(nxt)
            i += 2
        elif a[2:] in values and a.startswith("--") and nxt is not None:
            opts[a[2:]] = nxt
            i += 2
        else:
            raise E2EError(USAGE)
    return opts


def _parse_run(args: list[str]) -> dict[str, Any]:
    opts = _options(args, ("force", "all"), ("base", "diff", "task"))
    if opts.get("base") and opts.get("diff"):
        raise E2EError("use --base or --diff, not both\n" + USAGE)
    if opts.get("all") and len(opts) > 1:
        raise E2EError("--all takes no other option\n" + USAGE)
    return opts


def _parse_new(args: list[str]) -> NewScenario:
    if not args or args[0].startswith("--"):
        raise E2EError(USAGE)
    opts = _options(args[1:], (), ("method", "tool"), ("given", "when", "then"))
    if "method" not in opts:
        raise E2EError("--method is required\n" + USAGE)
    steps = Steps(*(tuple(opts.get(step, ())) for step in ("given", "when", "then")))
    return NewScenario(args[0], opts["method"], steps, opts.get("tool"))


def _read_diff(opts: dict[str, Any], cwd: str) -> str:
    if not opts.get("diff"):
        return working_diff(cwd, opts.get("base"))
    try:
        with open(os.path.join(cwd, opts["diff"]), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        raise E2EError(f"cannot read {opts['diff']}: {e}") from None


def _memory(cwd: str) -> str:
    return os.path.join(heimdall_dir(cwd), MEMORY)


def _last_green(cwd: str) -> str | None:
    try:
        with open(_memory(cwd), encoding="utf-8") as f:
            return str(json.load(f).get("green"))
    except (OSError, ValueError, AttributeError):
        return None


def _remember_green(cwd: str, fp: str) -> None:
    os.makedirs(heimdall_dir(cwd), exist_ok=True)
    with open(_memory(cwd), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"green": fp}, f)


@dataclass
class _Situation:
    """Everything the plan is made from: the changed files, the slice map, the scenarios and the task, and the
    scenarios whose own text or test the change touched."""

    files: list[FileChange]
    m: MapModel | None
    scenarios: list[Scenario]
    task: str | None
    cwd: str
    change: Change = field(init=False)
    edited: list[Scenario] = field(init=False)

    def __post_init__(self) -> None:
        self.change = Change.of(self.files, self.m)
        self.edited = [s for s in self.scenarios if own_files_changed(s, self.change)]

    def _tests(self, s: Scenario) -> dict[str, str]:
        return {p: Path(self.cwd, p).read_text(encoding="utf-8", errors="replace") for p in sorted(s.test_paths)}

    def payload(self) -> dict[str, Any]:
        """The review's state with the hunks of application files only — docs, tests and tooling cannot change
        what a client observes, and their hunks would crowd the judge's window — plus the scenarios, and the
        tests of each one whose text or test changed, for Jev to hold the two against each other."""
        state = build_state(self.files, self.m, self.task)
        state["files"] = [f if is_application(f["area"]) else {k: v for k, v in f.items() if k != "diff"} for f in state["files"]]
        state["scenarios"] = [s.as_state() | ({"tests": self._tests(s)} if s in self.edited else {}) for s in self.scenarios]
        qs = questions(self.scenarios, self.edited, self.change.application)
        return {"model": DEFAULT_MODEL, "state": state, "questions": qs}


def _transport(seams: Seams, out: TextIO) -> Transport | None:
    if seams.transport is not None:
        return seams.transport
    env = os.environ if seams.env is None else seams.env
    key = env.get(API_KEY_ENV, "")
    if key:
        return http_transport(key, env.get(API_URL_ENV, API_URL))
    out.write(
        f"heimdall e2e: {API_KEY_ENV} is not set — the slice map decides which scenarios rerun, "
        "and only their clauses hold a scenario's text to its test\n"
    )
    return None


def _ask(transport: Transport, sit: _Situation, out: TextIO) -> dict[str, Any] | None:
    """Jev's answers, or ``None`` after saying why there are none."""
    try:
        answers = transport(sit.payload()).get("answers")
    except ReviewError as e:
        out.write(f"heimdall e2e: Jev did not answer ({e})\n")
        return None
    if not isinstance(answers, dict):
        out.write("heimdall e2e: malformed Jev response (no `answers`)\n")
        return None
    return answers


def _answers(sit: _Situation, seams: Seams, out: TextIO) -> dict[str, Any] | None:
    transport = _transport(seams, out)
    return _ask(transport, sit, out) if transport is not None else None


def _plan(sit: _Situation, seams: Seams, out: TextIO) -> Plan:
    """Jev's plan, or the slice map's when no application code changed, there is no key, or Jev gives no verdict.
    Without application code, Jev is asked only whether each edited scenario's text still says what its test does."""
    if not sit.change.application:
        out.write("heimdall e2e: no application code changed — only scenarios whose own files changed rerun\n")
        plan = fallback(sit.scenarios, sit.change)
        answers = _answers(sit, seams, out) if sit.edited else None
        plan.drifted = drifted(answers, sit.scenarios, sit.edited) if answers is not None else []
        return plan
    answers = _answers(sit, seams, out)
    if answers is None:
        out.write("heimdall e2e: the slice map decides which scenarios rerun\n")
        return fallback(sit.scenarios, sit.change)
    return decide(answers, sit.scenarios, sit.change)


def _passes(chosen: list[Scenario], runner: Runner, out: TextIO) -> bool:
    """Whether every chosen scenario passed; a failure's runner output is written for the agent to act on."""
    out.write(f"heimdall e2e: running {len(chosen)} scenario(s): {', '.join(s.id for s in chosen)}\n")
    result = runner(chosen)
    if not result.ok:
        out.write(f"heimdall e2e: scenarios failed\n{result.output}\n")
    return result.ok


def _owed_lines(plan: Plan, problems: list[str], system: System) -> list[str]:
    """One line per scenario still to fix or write, each ending in what to do about it."""
    lines = [f"fix {p}" for p in problems]
    lines += [f"feature `{f}` changed and has no scenario: {new_advice(f, system)}" for f in plan.uncovered]
    if plan.unnamed is not None:
        lines.append(
            f"Jev: the change adds observable behaviour no scenario describes (p={plan.unnamed:.2f} ≥ {T['new_scenario']:.2f}): "
            + new_advice("<feature>", system)
        )
    lines += [
        f"Jev: {s.id}'s text and its test disagree (p={p:.2f} ≥ {T['drift']:.2f}): make the scenario in {s.spec_path} "
        f"say what {', '.join(sorted(s.test_paths))} does, or the test do what the scenario says"
        for s, p in plan.drifted
    ]
    return lines


def _owed(lines: list[str], out: TextIO) -> int:
    """After a green run: the scenarios still to write or fix, or green."""
    if lines:
        out.write(f"heimdall e2e: scenarios to write or fix (format: {GUIDE}):\n" + "".join(f"- {line}\n" for line in lines))
        return EXIT_NEEDS_SCENARIOS
    out.write("heimdall e2e: green\n")
    return EXIT_OK


def _verdict(chosen: list[Scenario], owed: list[str], runner: Runner, out: TextIO) -> int:
    """The run's exit code: the chosen scenarios first, then the scenarios still to write or fix."""
    if chosen and not _passes(chosen, runner, out):
        return EXIT_FAILED
    return _owed(owed, out)


def _load_map(cwd: str, err: TextIO) -> MapModel | None:
    try:
        return load_map(heimdall_dir(cwd))
    except UnreadableMap as e:
        err.write(f"heimdall e2e: unreadable .heimdall/map.json ({e}); every scenario is in reach\n")
        return None


def _idle(files: list[FileChange], fp: str, opts: dict[str, Any], cwd: str) -> str | None:
    """Why there is nothing to run, or ``None``: an empty change, or the one the last green run saw."""
    if not files:
        return "nothing changed"
    if not opts.get("force") and _last_green(cwd) == fp:
        return "unchanged since the last green run"
    return None


def _execute(opts: dict[str, Any], io: Streams, cwd: str, seams: Seams) -> int:
    """The scenarios the change reaches, run; a green verdict is remembered against the change."""
    diff = _read_diff(opts, cwd)
    files, fp = parse_unified_diff(diff), fingerprint(diff)
    idle = _idle(files, fp, opts, cwd)
    if idle:
        io.stdout.write(f"heimdall e2e: {idle}\n")
        return EXIT_OK
    system = load_system(cwd)
    scenarios, problems = load_scenarios(cwd, system)
    plan = _plan(_Situation(files, _load_map(cwd, io.stderr), scenarios, opts.get("task"), cwd), seams, io.stdout)
    if not plan.rerun:
        io.stdout.write("heimdall e2e: no scenario is reached by this change\n")
    code = _verdict(plan.rerun, _owed_lines(plan, problems, system), seams.runner or system_runner(cwd, system), io.stdout)
    if code == EXIT_OK:
        _remember_green(cwd, fp)
    return code


def _execute_all(io: Streams, cwd: str, seams: Seams) -> int:
    """Every scenario against the started system: the promise checked on every push, not only on an agent's stop."""
    system = load_system(cwd)
    scenarios, problems = load_scenarios(cwd, system)
    if not scenarios:
        io.stdout.write(f"heimdall e2e: no scenarios under {SCENARIOS_DIR}\n")
    return _verdict(scenarios, [f"fix {p}" for p in problems], seams.runner or system_runner(cwd, system), io.stdout)


def _new(args: list[str], io: Streams, cwd: str) -> int:
    written = scaffold(cwd, _parse_new(args), load_system(cwd))
    io.stdout.write("heimdall e2e new: wrote\n" + "".join(f"  {p}\n" for p in written))
    io.stdout.write("Each test fails until it asserts the `then`: write it, then let the Stop hook run it.\n")
    return EXIT_OK


def run(args: list[str], io: Streams, cwd: str, seams: Seams | None = None) -> int:
    try:
        if args[:1] == ["new"]:
            return _new(args[1:], io, cwd)
        opts = _parse_run(args)
        if opts.get("all"):
            return _execute_all(io, cwd, seams or Seams())
        return _execute(opts, io, cwd, seams or Seams())
    except E2EError as e:
        io.stderr.write(f"heimdall e2e: {e}\n")
        return EXIT_ERROR
