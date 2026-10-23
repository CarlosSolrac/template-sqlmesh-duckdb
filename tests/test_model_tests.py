"""Every SQLMesh model has a unit test in `tests/test_models.yaml`, so a new model cannot ship without one."""

import re
from pathlib import Path

from project.adapters.csv_source import is_mapping, safe_loader

ROOT: Path = Path(__file__).resolve().parents[1]
MODEL_NAME: re.Pattern[str] = re.compile(r"^\s*name\s+([\w.]+)\s*,", re.MULTILINE)


def untested_models(models_dir: Path, tests_file: Path) -> list[str]:
    """Return the models under `models_dir` that no test in `tests_file` names, sorted."""
    declared: set[str] = set()
    path: Path
    for path in models_dir.rglob("*.sql"):
        declared.update(MODEL_NAME.findall(path.read_text(encoding="utf-8")))
    loaded: object = safe_loader().load(tests_file)
    assert is_mapping(loaded)
    tested: set[str] = set()
    test: object
    for test in loaded.values():
        assert is_mapping(test)
        tested.add(str(test["model"]))
    return sorted(declared - tested)


def test_every_model_of_the_project_has_a_unit_test() -> None:
    assert untested_models(ROOT / "models", ROOT / "tests" / "test_models.yaml") == []


def test_a_model_without_a_unit_test_is_reported(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.sql").write_text("MODEL (\n  name orders.a,\n  kind FULL\n);\nSELECT 1\n", encoding="utf-8")
    (tmp_path / "models" / "b.sql").write_text("MODEL (\n  name orders.b,\n  kind FULL\n);\nSELECT 1\n", encoding="utf-8")
    tests_file: Path = tmp_path / "tests.yaml"
    tests_file.write_text("test_a:\n  model: orders.a\n", encoding="utf-8")

    assert untested_models(tmp_path / "models", tests_file) == ["orders.b"]
