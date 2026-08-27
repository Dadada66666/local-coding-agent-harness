from benchmarks.cases import BenchmarkCase
from benchmarks.evaluator import changed_paths, evaluate_case, snapshot_workspace


def test_snapshot_workspace_ignores_runtime_and_python_cache(tmp_path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".agent" / "runs").mkdir(parents=True)
    (tmp_path / ".agent" / "runs" / "trace.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "module.pyc").write_bytes(b"cache")

    assert set(snapshot_workspace(tmp_path)) == {"module.py"}


def test_changed_paths_detects_created_modified_and_deleted_files() -> None:
    before = {"deleted.py": "a", "modified.py": "a"}
    after = {"created.py": "b", "modified.py": "b"}

    assert changed_paths(before, after) == (
        "created.py",
        "deleted.py",
        "modified.py",
    )


def test_evaluate_case_rejects_change_outside_allowed_paths(tmp_path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "test_module.py").write_text(
        "from module import value\n\ndef test_value():\n    assert value == 1\n",
        encoding="utf-8",
    )
    before = snapshot_workspace(tmp_path)
    (tmp_path / "notes.txt").write_text("unexpected\n", encoding="utf-8")
    after = snapshot_workspace(tmp_path)
    case = BenchmarkCase(
        case_id="allowed-paths",
        title="Allowed paths",
        fixture=tmp_path,
        task="test",
        difficulty="smoke",
        category="mutation",
        allowed_changed_paths=("module.py",),
    )

    result = evaluate_case(case, tmp_path, before, after)

    assert result.pytest_passed is True
    assert result.passed is False
    assert result.unauthorized_mutations == ("notes.txt",)
