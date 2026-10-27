"""Executable eval gate for CI: the full dataset against the scripted client must score 100 %."""

from pathlib import Path

import pytest

from evals.__main__ import main

pytestmark = pytest.mark.evals


def test_scripted_eval_gate(tmp_path: Path):
    assert main(["--client", "scripted", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.md").exists() and (tmp_path / "report.json").exists()
    assert (tmp_path / "results.jsonl").read_text().count("\n") == 11
    assert (tmp_path / "errors.jsonl").read_text() == ""
    assert len(list((tmp_path / "trajectories").glob("*.json"))) == 11
