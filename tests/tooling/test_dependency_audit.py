"""``tools/dependency_audit.py``: an advisory with a fixed release fails the audit; one without is reported."""

from __future__ import annotations

from dependency_audit import findings


def test_advisories_split_into_fixable_and_unfixed() -> None:
    report = {
        "dependencies": [
            {"name": "starlette", "version": "0.1", "vulns": [{"id": "GHSA-1", "fix_versions": ["0.2", "0.3"]}]},
            {"name": "torch", "version": "2.0", "vulns": [{"id": "PYSEC-9", "fix_versions": []}]},
            {"name": "numpy", "version": "2.1", "vulns": []},
        ]
    }

    assert findings(report) == (["starlette 0.1: GHSA-1 — fixed in 0.2, 0.3"], ["torch 2.0: PYSEC-9 — no fixed release yet"])


def test_an_advisory_listed_twice_is_reported_once() -> None:
    vuln = {"id": "PYSEC-2025-49", "fix_versions": ["78.1.1"]}
    report = {"dependencies": [{"name": "setuptools", "version": "78.1.0", "vulns": [vuln, dict(vuln)]}]}

    assert findings(report) == (["setuptools 78.1.0: PYSEC-2025-49 — fixed in 78.1.1"], [])


def test_a_clean_report_has_nothing_to_fix() -> None:
    assert findings({"dependencies": [{"name": "fastapi", "version": "1.0", "vulns": []}]}) == ([], [])
