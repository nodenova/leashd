"""Tests for JSONL task event logging."""

from __future__ import annotations

import json

import pytest

from leashd.core import task_events


class TestAppend:
    def test_creates_file_and_writes_event(self, tmp_path):
        ok = task_events.append(
            "run1", str(tmp_path), {"event": "task_created", "task": "hello"}
        )
        assert ok is True
        fp = tmp_path / ".leashd" / "tasks" / "run1.jsonl"
        assert fp.is_file()
        lines = fp.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "task_created"
        assert entry["task"] == "hello"
        assert "ts" in entry

    def test_appends_multiple_events(self, tmp_path):
        task_events.append("run1", str(tmp_path), {"event": "a"})
        task_events.append("run1", str(tmp_path), {"event": "b"})
        task_events.append("run1", str(tmp_path), {"event": "c"})
        fp = tmp_path / ".leashd" / "tasks" / "run1.jsonl"
        lines = fp.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_rejects_path_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid run_id"):
            task_events.append("../etc/passwd", str(tmp_path), {"event": "x"})

    def test_returns_false_on_write_failure(self, tmp_path):
        # Point to a non-existent path that can't be created
        ok = task_events.append("run1", "/dev/null/impossible", {"event": "x"})
        assert ok is False


class TestReadAll:
    def test_returns_empty_for_missing_file(self, tmp_path):
        events = task_events.read_all("missing", str(tmp_path))
        assert events == []

    def test_reads_back_events(self, tmp_path):
        task_events.append("run1", str(tmp_path), {"event": "a", "x": 1})
        task_events.append("run1", str(tmp_path), {"event": "b", "x": 2})
        events = task_events.read_all("run1", str(tmp_path))
        assert len(events) == 2
        assert events[0]["event"] == "a"
        assert events[1]["event"] == "b"

    def test_skips_malformed_lines(self, tmp_path):
        fp = tmp_path / ".leashd" / "tasks" / "run1.jsonl"
        fp.parent.mkdir(parents=True)
        fp.write_text('{"event":"a"}\nnot-json\n{"event":"b"}\n')
        events = task_events.read_all("run1", str(tmp_path))
        assert len(events) == 2
        assert events[0]["event"] == "a"
        assert events[1]["event"] == "b"

    def test_handles_empty_file(self, tmp_path):
        fp = tmp_path / ".leashd" / "tasks" / "run1.jsonl"
        fp.parent.mkdir(parents=True)
        fp.write_text("")
        events = task_events.read_all("run1", str(tmp_path))
        assert events == []
