from __future__ import annotations

import ast
import textwrap

from eitri.core import EitriConfig, _mini_toml_section
from eitri.surface import render_module_surface


def _render(code: str) -> str:
    return render_module_surface(ast.parse(textwrap.dedent(code)))


def test_bodies_are_stripped_and_private_names_dropped() -> None:
    out = _render(
        """
        from dataclasses import dataclass

        _SECRET = 1
        VERSION = "1.0"

        @dataclass(frozen=True)
        class Verse:
            score: float
            notes: list[str] = None

            def pretty(self) -> str:
                return f"{self.score}"

            def _hidden(self):
                return 1

        def compose(x: int, *, y: str = "a") -> Verse:
            body = x + 1
            return Verse(body, [])

        def _helper():
            pass
        """
    )
    assert "_SECRET" not in out
    assert "_hidden" not in out
    assert "_helper" not in out
    assert "body = x + 1" not in out
    assert "VERSION = '1.0'" in out
    assert "@dataclass(frozen=True)" in out
    assert "score: float" in out
    assert "notes: list[str]" in out
    assert "def pretty(self) -> str:" in out
    assert "def compose(x: int, *, y: str='a') -> Verse:" in out


def test_dunder_methods_are_part_of_the_surface() -> None:
    out = _render(
        """
        class Engine:
            def __init__(self, seed: int) -> None:
                self.seed = seed
        """
    )
    assert "def __init__(self, seed: int) -> None:" in out


def test_dunder_all_wins_over_underscore_convention() -> None:
    out = _render(
        """
        __all__ = ["Exported"]
        class Exported: ...
        class NotExported: ...
        """
    )
    assert "Exported" in out and "NotExported" not in out


def test_empty_module_renders_empty() -> None:
    assert _render("x = f()\n_y = 1\n") == "x\n"
    assert _render("") == ""


def test_mini_toml_reader_handles_the_eitri_section() -> None:
    text = """
    [project]
    name = "x"
    token_budget = 1   # not ours

    [tool.eitri]
    slice_prefix = "slices."   # trailing comment
    kernel = 'shared_kernel'
    token_budget = 9_000
    enabled = false
    contract_allowed_modules = ["pydantic", 'attrs']

    [tool.other]
    token_budget = 2
    """
    section = _mini_toml_section(textwrap.dedent(text), "tool.eitri")
    assert section == {
        "slice_prefix": "slices.",
        "kernel": "shared_kernel",
        "token_budget": 9000,
        "enabled": False,
        "contract_allowed_modules": ["pydantic", "attrs"],
    }
    cfg = EitriConfig._from_values(section)
    assert cfg.token_budget == 9000 and not cfg.enabled and cfg.contract_allowed_modules == ("pydantic", "attrs")


def test_config_read_from_pyproject_with_overrides(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.eitri]\ntoken_budget = 500\nkernel = "core"\n', encoding="utf-8")
    cfg = EitriConfig.read(tmp_path, token_budget=None, kernel="k2")
    assert cfg.token_budget == 500
    assert cfg.kernel == "k2"
    assert cfg.slices_package == "slices"
