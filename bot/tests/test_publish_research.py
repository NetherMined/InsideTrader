"""Test Results publish tool: reading and validating bot/results/*.json.

Run: python -m pytest bot/tests/test_publish_research.py
"""
import json

import pytest

from bot.tools.publish_research import DEFAULT_DIR, read_results


def _write(tmp_path, name, doc):
    (tmp_path / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_reads_valid_files_in_name_order(tmp_path):
    _write(tmp_path, "b_set", {"key": "b_set", "title": "B", "rows": [{"x": 1}]})
    _write(tmp_path, "a_set", {"key": "a_set", "title": "A", "phase": "Phase 1", "rows": []})
    docs = read_results(tmp_path)
    assert [d["key"] for d in docs] == ["a_set", "b_set"]
    assert docs[0]["phase"] == "Phase 1" and docs[1]["description"] is None


def test_rejects_missing_fields_and_mismatched_key(tmp_path):
    _write(tmp_path, "bad", {"key": "bad", "rows": []})
    with pytest.raises(ValueError, match="missing title"):
        read_results(tmp_path)
    (tmp_path / "bad.json").unlink()
    _write(tmp_path, "file_name", {"key": "other", "title": "X", "rows": []})
    with pytest.raises(ValueError, match="must match the file name"):
        read_results(tmp_path)


def test_committed_results_are_valid():
    docs = read_results(DEFAULT_DIR)
    keys = {d["key"] for d in docs}
    assert {"phases", "daily_strategies", "daytrade_setups", "exit_replay"} <= keys
    assert all(d["rows"] for d in docs)
