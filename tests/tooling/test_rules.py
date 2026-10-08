"""The rule tests — port of RuleTests.cs onto the import-graph walls."""

from __future__ import annotations

from conftest import TempProject

from eitri import EitriConfig, analyze
from eitri.analyzer import EitriError


def _only(ids: list[str], rule: str) -> list[str]:
    return [i for i in ids if i == rule]


# ---------------------------------------------------------------- EIT001


class TestEit001:
    def test_import_of_foreign_internal_is_reported(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": "", "internal/rune_engine.py": "class RuneEngine: ..."})
        project.slice(
            "kvad",
            ["rune"],
            **{"internal/__init__.py": "", "internal/leak.py": "from pfa.features.rune.internal.rune_engine import RuneEngine\n"},
        )
        diags = project.analyze()
        hits = [d for d in diags if d.id == "EIT001"]
        assert len(hits) == 1
        assert hits[0].line == 1
        assert hits[0].path is not None and hits[0].path.name == "leak.py"
        assert "pfa.features.rune.internal.rune_engine.RuneEngine" in hits[0].message
        assert "internal to slice 'rune'" in hits[0].message

    def test_import_of_foreign_contract_is_fine(self, project: TempProject) -> None:
        project.slice("rune", **{"contract/__init__.py": "class RuneService: ..."})
        project.slice(
            "kvad", ["rune"], **{"internal/__init__.py": "", "internal/svc.py": "from pfa.features.rune.contract import RuneService\n"}
        )
        assert "EIT001" not in project.ids()

    def test_another_slices_module_is_internal_to_it(self, project: TempProject) -> None:
        """``module.py`` is the plug the composition root uses; for a sibling slice it is just more internals."""
        project.slice("rune", **{"module.py": "def register(r): ..."})
        project.slice("kvad", ["rune"], **{"internal/__init__.py": "", "internal/x.py": "from pfa.features.rune.module import register\n"})
        assert project.ids().count("EIT001") == 1

    def test_the_application_may_import_a_slices_contract_anywhere_and_its_module_only_from_the_composition_root(
        self, project: TempProject
    ) -> None:
        project.slice(
            "rune",
            **{
                "contract/__init__.py": "class RuneService: ...",
                "module.py": "def register(r): ...",
                "internal/rune_engine.py": "class RuneEngine: ...",
            },
        )
        project.app(
            **{
                "application.py": "from pfa.features.rune import module as rune\nfrom pfa.features.rune.contract import RuneService\n",
                "api/routes/rune.py": "from pfa.features.rune.contract import RuneService\n",
                "api/plug.py": "from pfa.features.rune import module as rune\n",
                "api/leak.py": "from pfa.features.rune.internal.rune_engine import RuneEngine\nfrom pfa.features.rune.internal import rune_engine\n",
            }
        )
        hits = [d for d in project.analyze() if d.id == "EIT001"]
        assert sorted((d.path.name, d.line) for d in hits) == [("leak.py", 1), ("leak.py", 2), ("plug.py", 1)]
        assert "internal to slice 'rune'" in hits[0].message

    def test_the_application_package_and_composition_root_come_from_config(self, project: TempProject) -> None:
        project.slice("rune", **{"module.py": "", "internal/__init__.py": "", "internal/rune_engine.py": "class RuneEngine: ..."})
        project.write("service/wiring.py", "from pfa.features.rune import module as rune\n")
        project.write("service/leak.py", "from pfa.features.rune.internal.rune_engine import RuneEngine\n")
        assert "EIT001" not in project.ids()
        assert project.ids(app_package="service", composition_root="service.wiring").count("EIT001") == 1
        assert project.ids(app_package="service", composition_root="service.other").count("EIT001") == 2

    def test_other_packages_are_not_policed(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": "", "internal/rune_engine.py": "class RuneEngine: ..."})
        project.write("other/thing.py", "from pfa.features.rune.internal.rune_engine import RuneEngine\n")
        assert "EIT001" not in project.ids()

    def test_own_slice_internal_import_is_fine(self, project: TempProject) -> None:
        project.slice(
            "kvad",
            **{
                "internal/__init__.py": "",
                "internal/engine.py": "class Engine: ...",
                "internal/svc.py": "from pfa.features.kvad.internal.engine import Engine\nfrom .engine import Engine as E2\n",
            },
        )
        assert "EIT001" not in project.ids()

    def test_lazy_import_inside_function_is_still_caught(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": "", "internal/rune_engine.py": "class RuneEngine: ..."})
        project.slice(
            "kvad",
            ["rune"],
            **{
                "internal/__init__.py": "",
                "internal/leak.py": "def sneaky():\n    from pfa.features.rune.internal import rune_engine\n    return rune_engine\n",
            },
        )
        hits = [d for d in project.analyze() if d.id == "EIT001"]
        assert len(hits) == 1 and hits[0].line == 2


# ---------------------------------------------------------------- EIT002


class TestEit002:
    def test_importlib_is_reported(self, project: TempProject) -> None:
        project.slice(
            "rune",
            **{
                "internal/__init__.py": "",
                "internal/hatch.py": "import importlib\nx = importlib.import_module('pfa.features.kvad.internal.engine')\n",
            },
        )
        hits = [d for d in project.analyze() if d.id == "EIT002"]
        assert len(hits) == 1 and hits[0].line == 2
        assert "importlib.import_module" in hits[0].message

    def test_dunder_import_is_reported(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": "", "internal/hatch.py": "m = __import__('slices.kvad')\n"})
        assert _only(project.ids(), "EIT002") == ["EIT002"]

    def test_sys_path_surgery_is_reported(self, project: TempProject) -> None:
        project.slice(
            "rune",
            **{
                "internal/__init__.py": "",
                "internal/hatch.py": "import sys\nsys.path.insert(0, '..')\nsys.path = []\nsys.modules['x'] = None\n",
            },
        )
        assert _only(project.ids(), "EIT002") == ["EIT002"] * 3

    def test_wildcard_import_is_reported(self, project: TempProject) -> None:
        project.slice("rune", **{"contract/__init__.py": "class RuneService: ..."})
        project.slice("kvad", ["rune"], **{"internal/__init__.py": "", "internal/x.py": "from pfa.features.rune.contract import *\n"})
        hits = [d for d in project.analyze() if d.id == "EIT002"]
        assert len(hits) == 1 and "import *" in hits[0].message

    def test_plain_imports_are_fine(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": "", "internal/ok.py": "import sys\nimport json\nprint(sys.path)\n"})
        assert "EIT002" not in project.ids()


# ---------------------------------------------------------------- EIT003


class TestEit003:
    def test_contract_exposing_foreign_contract_type_is_reported(self, project: TempProject) -> None:
        project.slice("rune", **{"contract/__init__.py": "class RuneReading: ..."})
        project.slice("kvad", ["rune"], **{"contract/__init__.py": "from pfa.features.rune.contract import RuneReading\n"})
        hits = [d for d in project.analyze() if d.id == "EIT003"]
        assert len(hits) == 1
        assert "pfa.features.kvad.contract" in hits[0].message and "pfa.features.rune.contract.RuneReading" in hits[0].message

    def test_contract_using_stdlib_kernel_and_same_contract_is_fine(self, project: TempProject) -> None:
        project.slice(
            "kvad",
            **{
                "contract/__init__.py": "from .types import Verse\nfrom .service import KvadService\n",
                "contract/types.py": "from __future__ import annotations\nfrom dataclasses import dataclass\nfrom pfa.kernel import StaveId\n@dataclass\nclass Verse:\n    stave_id: StaveId\n",
                "contract/service.py": "from typing import Protocol\nfrom pfa.features.kvad.contract.types import Verse\nclass KvadService(Protocol):\n    def compose(self) -> Verse: ...\n",
            },
        )
        assert "EIT003" not in project.ids()

    def test_contract_importing_third_party_is_reported(self, project: TempProject) -> None:
        project.slice("kvad", **{"contract/__init__.py": "import pydantic\n"})
        assert "EIT003" in project.ids()

    def test_contract_allowed_modules_whitelists_third_party(self, project: TempProject) -> None:
        project.slice("kvad", **{"contract/__init__.py": "import pydantic\n"})
        assert "EIT003" not in project.ids(contract_allowed_modules=("pydantic",))

    def test_contract_importing_own_internal_is_reported(self, project: TempProject) -> None:
        project.slice(
            "kvad",
            **{"contract/__init__.py": "from pfa.features.kvad.internal.engine import Engine\n", "internal/engine.py": "class Engine: ..."},
        )
        assert "EIT003" in project.ids()


# ---------------------------------------------------------------- EIT004


class TestEit004:
    def test_undeclared_contract_dependency_is_reported(self, project: TempProject) -> None:
        project.slice("rune", **{"contract/__init__.py": "class RuneService: ..."})
        project.slice("kvad", [], **{"internal/__init__.py": "", "internal/svc.py": "from pfa.features.rune.contract import RuneService\n"})
        hits = [d for d in project.analyze() if d.id == "EIT004"]
        assert len(hits) == 1
        assert "'kvad' imports the Contract of 'rune'" in hits[0].message
        assert "feature.json" in hits[0].message

    def test_declared_contract_dependency_is_fine(self, project: TempProject) -> None:
        project.slice("rune", **{"contract/__init__.py": "class RuneService: ..."})
        project.slice(
            "kvad", ["rune"], **{"internal/__init__.py": "", "internal/svc.py": "from pfa.features.rune.contract import RuneService\n"}
        )
        assert "EIT004" not in project.ids()

    def test_missing_manifest_means_leaf_slice(self, project: TempProject) -> None:
        project.slice("rune", **{"contract/__init__.py": "class RuneService: ..."})
        project.write("pfa/features/kvad/__init__.py", "")
        project.write("pfa/features/kvad/internal/__init__.py", "")
        project.write("pfa/features/kvad/internal/svc.py", "from pfa.features.rune.contract import RuneService\n")
        assert "EIT004" in project.ids()

    def test_broken_manifest_is_a_hard_error(self, project: TempProject) -> None:
        project.write("pfa/features/kvad/feature.json", "{not json")
        try:
            project.analyze()
        except EitriError as e:
            assert "feature.json" in str(e)
        else:  # pragma: no cover
            raise AssertionError("expected EitriError")


# ---------------------------------------------------------------- EIT005


class TestEit005:
    """HTTPException is policed in the app package — the HTTP edge — never in a slice (there, EIT006 speaks)."""

    def test_importing_fastapi_http_exception_is_reported(self, project: TempProject) -> None:
        project.app(
            **{
                "routes/rune.py": """
                from fastapi import APIRouter, HTTPException, Request

                def split():
                    raise HTTPException(status_code=422, detail="empty text")
                """,
            },
        )
        hits = [d for d in project.analyze() if d.id == "EIT005"]
        assert len(hits) == 1 and hits[0].line == 1
        assert "fastapi.HTTPException" in hits[0].message and "'pfa.api.problems.Problem'" in hits[0].message

    def test_the_remedy_comes_from_config(self, project: TempProject) -> None:
        project.app(**{"routes/r.py": "from fastapi import HTTPException\n"})
        hits = [d for d in project.analyze(problem_helper="api_errors.Fault") if d.id == "EIT005"]
        assert len(hits) == 1 and "'api_errors.Fault'" in hits[0].message and "pfa.api.problems" not in hits[0].message

    def test_starlette_and_submodule_spellings_are_reported(self, project: TempProject) -> None:
        project.app(
            **{
                "a.py": "from starlette.exceptions import HTTPException\n",
                "b.py": "from fastapi.exceptions import HTTPException as HttpError\n",
            },
        )
        assert project.ids().count("EIT005") == 2

    def test_attribute_access_on_the_package_is_reported(self, project: TempProject) -> None:
        project.app(**{"routes/rune.py": "import fastapi\n\ndef f():\n    raise fastapi.HTTPException(404)\n"})
        hits = [d for d in project.analyze() if d.id == "EIT005"]
        assert len(hits) == 1 and hits[0].line == 4

    def test_aliased_spellings_cannot_dodge_the_wall(self, project: TempProject) -> None:
        project.app(
            **{
                "a.py": "import fastapi as fa\n\ndef f():\n    raise fa.HTTPException(422)\n",
                "b.py": "from fastapi import exceptions\n\ndef f():\n    raise exceptions.HTTPException(422)\n",
                "c.py": "import starlette.exceptions as se\n\ndef f():\n    raise se.HTTPException(422)\n",
                "d.py": "import fastapi.exceptions\n\ndef f():\n    raise fastapi.exceptions.HTTPException(422)\n",
            },
        )
        hits = sorted((d.path.name, d.line, d.message.split("'")[3]) for d in project.analyze() if d.id == "EIT005")
        assert hits == [
            ("a.py", 4, "fastapi.HTTPException"),
            ("b.py", 4, "fastapi.exceptions.HTTPException"),
            ("c.py", 4, "starlette.exceptions.HTTPException"),
            ("d.py", 4, "fastapi.exceptions.HTTPException"),
        ]

    def test_raising_the_shared_problem_is_fine(self, project: TempProject) -> None:
        project.app(
            **{
                "routes/rune.py": """
                from fastapi import APIRouter, Request
                from pfa.api.problems import Problem

                def split(result):
                    if not result.ok:
                        raise Problem.domain_rejection(result.error)
                """,
            },
        )
        assert "EIT005" not in project.ids()

    def test_the_problem_helpers_own_module_is_the_conversion_point(self, project: TempProject) -> None:
        """The module that defines the helper must see HTTPException to turn it into a problem document."""
        project.app(**{"api/problems.py": "from starlette.exceptions import HTTPException\n\nclass Problem(Exception): ...\n"})
        assert "EIT005" not in project.ids()
        assert project.ids(problem_helper="pfa.api.errors.Problem").count("EIT005") == 1

    def test_in_a_slice_eit006_speaks_instead(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": "", "internal/r.py": "from fastapi import HTTPException\n"})
        ids = project.ids()
        assert "EIT005" not in ids and ids.count("EIT006") == 1

    def test_outside_slices_and_app_is_not_policed(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": ""})
        project.write("other/problems.py", "from starlette.exceptions import HTTPException\n")
        assert "EIT005" not in project.ids()


# ---------------------------------------------------------------- EIT006


class TestEit006:
    def test_a_slice_importing_fastapi_is_reported(self, project: TempProject) -> None:
        project.slice(
            "rune",
            **{
                "internal/__init__.py": "",
                "internal/routes.py": "from fastapi import APIRouter\n\nrouter = APIRouter(prefix='/rune')\n",
            },
        )
        hits = [d for d in project.analyze() if d.id == "EIT006"]
        assert len(hits) == 1 and hits[0].line == 1 and hits[0].path is not None and hits[0].path.name == "routes.py"
        assert "fastapi.APIRouter" in hits[0].message and "'pfa'" in hits[0].message

    def test_starlette_and_lazy_imports_are_reported(self, project: TempProject) -> None:
        project.slice(
            "rune",
            **{
                "internal/__init__.py": "",
                "internal/a.py": "import starlette.requests\n",
                "internal/b.py": "def f():\n    from fastapi import Request\n    return Request\n",
                "module.py": "import fastapi as fa\n",
            },
        )
        hits = sorted((d.path.name, d.line) for d in project.analyze() if d.id == "EIT006")
        assert hits == [("a.py", 1), ("b.py", 2), ("module.py", 1)]

    def test_in_a_contract_only_eit003_speaks(self, project: TempProject) -> None:
        project.slice("rune", **{"contract/__init__.py": "", "contract/c.py": "from fastapi import APIRouter\n"})
        ids = project.ids()
        assert "EIT003" in ids and "EIT006" not in ids

    def test_the_remedy_names_the_app_package_from_config(self, project: TempProject) -> None:
        project.slice("rune", **{"internal/__init__.py": "", "internal/r.py": "from fastapi import APIRouter\n"})
        hits = [d for d in project.analyze(app_package="service") if d.id == "EIT006"]
        assert len(hits) == 1 and "'service'" in hits[0].message

    def test_pydantic_and_the_application_are_not_eit006(self, project: TempProject) -> None:
        """EIT006 is about depending upward; a validation library in internal/ is allowed, and the
        application's entry point is where FastAPI belongs."""
        project.slice("rune", **{"internal/__init__.py": "", "internal/r.py": "from pydantic import BaseModel\n"})
        project.app(**{"api/routes/rune.py": "from fastapi import APIRouter\n"})
        assert "EIT006" not in project.ids()

    def test_a_slice_importing_the_application_or_its_entry_point_is_reported(self, project: TempProject) -> None:
        """The kernel is the only thing above a slice it may import; the application and the API are not."""
        project.slice(
            "rune",
            **{
                "internal/__init__.py": "",
                "internal/a.py": "from pfa.api.problems import Problem\n",
                "internal/b.py": "from pfa import application\n",
                "internal/c.py": "from pfa.kernel import Result\nimport pfa.features.rune.contract\n",
                "contract/__init__.py": "from pfa.api.problems import Problem\n",
            },
        )
        hits = sorted((d.path.name, d.id) for d in project.analyze() if d.id in ("EIT003", "EIT006"))
        assert hits == [("__init__.py", "EIT003"), ("a.py", "EIT006"), ("b.py", "EIT006")]


# ---------------------------------------------------------------- EIT100 / EIT101


class TestEit100:
    def test_over_budget_slice_fails(self, project: TempProject) -> None:
        project.slice(
            "kvad",
            **{
                "internal/__init__.py": "",
                "internal/engine.py": "class Engine:\n    def compute_weighted_reading_score(self, rune_count, utterance_length):\n        return rune_count * 31 + utterance_length * 7\n",
            },
        )
        diags = project.analyze(budget=10)
        assert [d.id for d in diags if d.descriptor.category == "Eitri.ContextBudget"] == ["EIT100"]
        d = next(d for d in diags if d.id == "EIT100")
        assert d.severity.value == "error"
        assert "Slice 'kvad' worst-case agent working set" in d.message
        assert "over the budget of 10" in d.message

    def test_under_budget_slice_reports_info(self, project: TempProject) -> None:
        project.slice("kvad", **{"internal/__init__.py": "", "internal/engine.py": "class Engine: ...\n"})
        diags = project.analyze(budget=100_000)
        assert [d.id for d in diags] == ["EIT101"]
        assert diags[0].severity.value == "info"
        assert "of 100,000 budget" in diags[0].message

    def test_non_slice_directory_is_not_metered(self, project: TempProject) -> None:
        project.write("just_a_library/thing.py", "class Thing: ...\n" * 50)
        assert project.ids(budget=10) == []

    def test_dependency_internals_cost_zero(self, project: TempProject) -> None:
        """Only Rune's *contract surface* lands in Kvad's working set — its internals are free."""
        project.slice("rune", **{"contract/__init__.py": "class RuneService:\n    def read(self) -> int: ...\n"})
        project.slice(
            "kvad", ["rune"], **{"internal/__init__.py": "", "internal/x.py": "from pfa.features.rune.contract import RuneService\n"}
        )
        before = next(d for d in project.analyze() if "'kvad'" in d.message)
        project.write("pfa/features/rune/internal/huge.py", "def f(): return 1\n" * 4000)
        after = next(d for d in project.analyze() if "'kvad'" in d.message)
        assert before.message == after.message
        rune = next(d for d in project.analyze() if "'rune'" in d.message)
        assert rune.id == "EIT100"  # Rune itself, of course, blew its own budget

    def test_contract_surface_counts_only_public_signatures(self, project: TempProject) -> None:
        project.slice(
            "rune",
            **{
                "contract/__init__.py": "class RuneService:\n    def read(self, x: int) -> int:\n        "
                + "y = 1\n        " * 200
                + "return y\n    def _hidden(self): ...\n",
            },
        )
        project.slice("kvad", ["rune"], **{"internal/__init__.py": ""})
        kvad = next(d for d in project.analyze() if "'kvad'" in d.message)
        # the 200-line body and the private method are not part of what an agent loads to consume Rune
        contracts = int(kvad.message.split("contracts ")[1].split(" ")[0].replace(",", ""))
        assert contracts < 40

    def test_disabled_config_reports_nothing(self, project: TempProject) -> None:
        project.slice("kvad", **{"internal/__init__.py": "", "internal/leak.py": "import importlib\nimportlib.import_module('x')\n"})
        assert analyze(project.root, EitriConfig(enabled=False)) == []


# ---------------------------------------------------------------- EIT000 / layout


class TestLayout:
    def test_unparsable_source_is_reported_as_eit000(self, project: TempProject) -> None:
        project.slice("kvad", **{"internal/__init__.py": "", "internal/broken.py": "def (:\n"})
        ids = project.ids()
        assert "EIT000" in ids

    def test_missing_slices_dir_is_a_hard_error(self, tmp_path) -> None:
        try:
            analyze(tmp_path, EitriConfig())
        except EitriError as e:
            assert "no pfa/features/ under" in str(e)
        else:  # pragma: no cover
            raise AssertionError("expected EitriError")

    def test_src_layout_is_found(self, tmp_path) -> None:
        (tmp_path / "src" / "pfa" / "features" / "kvad" / "internal").mkdir(parents=True)
        (tmp_path / "src" / "pfa" / "features" / "kvad" / "feature.json").write_text("{}", encoding="utf-8")
        (tmp_path / "src" / "pfa" / "features" / "kvad" / "internal" / "x.py").write_text(
            "import importlib\nimportlib.import_module('y')\n", encoding="utf-8"
        )
        assert "EIT002" in [d.id for d in analyze(tmp_path, EitriConfig())]
