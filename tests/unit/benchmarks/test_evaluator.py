from benchmarks.evaluator import changed_paths, snapshot_workspace


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
