"""Tests for daio.audit — logger, report, rollback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from daio.audit.logger import AuditLogger, load_audit_log
from daio.audit.report import generate_report, save_report, save_results_json
from daio.audit.rollback import rollback_all, rollback_function, strip_all_anchors


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ANCHORED_SOURCE = """\
import math

# UID:aaa111bbb222:START
def add(a, b):
    return a + b
# UID:aaa111bbb222:END
"""


@pytest.fixture()
def log_path(tmp_path: Path) -> Path:
    """Return a temp path for the audit log."""
    return tmp_path / ".daio" / "audit.jsonl"


@pytest.fixture()
def sample_results() -> dict[str, Any]:
    """Sample Surgeon results for testing."""
    return {
        "aaa111bbb222": {
            "uid": "aaa111bbb222",
            "function": "add",
            "file": "src/math.py",
            "status": "SUCCESS",
            "retries": 0,
            "errors": [],
            "commit_hash": "abc1234",
            "duration_seconds": 12.5,
        },
        "ccc333ddd444": {
            "uid": "ccc333ddd444",
            "function": "multiply",
            "file": "src/math.py",
            "status": "FAILED",
            "retries": 3,
            "errors": ["Attempt 1: SyntaxError", "Attempt 2: LOC shrinkage"],
            "commit_hash": None,
            "duration_seconds": 45.2,
        },
    }


@pytest.fixture()
def sample_manifest() -> dict[str, Any]:
    """Sample manifest for testing."""
    return {
        "base_path": "/tmp/test_project",
        "files": {
            "src/math.py": {
                "functions": [
                    {"name": "add", "uid": "aaa111bbb222", "status": "SUCCESS", "nested": False},
                    {"name": "multiply", "uid": "ccc333ddd444", "status": "FAILED", "nested": False},
                    {"name": "inner", "uid": "eee555fff666", "status": "SKIPPED_NESTED", "nested": True},
                ],
            },
        },
    }


# ===================================================================
# Logger Tests
# ===================================================================


class TestAuditLogger:
    """Tests for the JSONL audit logger."""

    def test_creates_log_file(self, log_path: Path) -> None:
        """Logger should create parent directories and log file."""
        logger = AuditLogger(log_path)
        logger.log("TEST", "abc123def456", "test_func", "test.py")
        assert log_path.exists()

    def test_appends_jsonl(self, log_path: Path) -> None:
        """Multiple log calls should append separate JSON lines."""
        logger = AuditLogger(log_path)
        logger.log("EVENT1", "uid1", "func1", "a.py")
        logger.log("EVENT2", "uid2", "func2", "b.py")

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

        record1 = json.loads(lines[0])
        assert record1["event"] == "EVENT1"
        assert record1["uid"] == "uid1"

        record2 = json.loads(lines[1])
        assert record2["event"] == "EVENT2"

    def test_record_has_required_fields(self, log_path: Path) -> None:
        """Each record should have timestamp, event, uid, function, file, data."""
        logger = AuditLogger(log_path)
        logger.log("DISPATCH", "abc123", "my_func", "src/mod.py", {"model": "qwen"})

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        record = json.loads(lines[0])

        assert "timestamp" in record
        assert "elapsed_seconds" in record
        assert record["event"] == "DISPATCH"
        assert record["uid"] == "abc123"
        assert record["function"] == "my_func"
        assert record["file"] == "src/mod.py"
        assert record["data"]["model"] == "qwen"

    def test_pipeline_start_end(self, log_path: Path) -> None:
        """Pipeline start and end events should be loggable."""
        logger = AuditLogger(log_path)
        logger.log_pipeline_start({"model": "test"})
        logger.log_pipeline_end({"succeeded": 5, "failed": 1})

        events = load_audit_log(log_path)
        assert events[0]["event"] == "PIPELINE_START"
        assert events[1]["event"] == "PIPELINE_END"
        assert events[1]["data"]["succeeded"] == 5

    def test_data_defaults_to_empty_dict(self, log_path: Path) -> None:
        """Omitting data should produce empty dict, not None."""
        logger = AuditLogger(log_path)
        logger.log("TEST", "uid", "func", "f.py")

        events = load_audit_log(log_path)
        assert events[0]["data"] == {}


class TestLoadAuditLog:
    """Tests for audit log loading."""

    def test_load_valid_log(self, log_path: Path) -> None:
        """Should parse all lines as JSON."""
        logger = AuditLogger(log_path)
        logger.log("A", "1", "f1", "a.py")
        logger.log("B", "2", "f2", "b.py")

        events = load_audit_log(log_path)
        assert len(events) == 2

    def test_load_nonexistent_raises(self) -> None:
        """Missing log file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_audit_log(Path("/nonexistent/audit.jsonl"))


# ===================================================================
# Report Tests
# ===================================================================


class TestGenerateReport:
    """Tests for report generation."""

    def test_report_contains_header(self, sample_manifest: dict, sample_results: dict) -> None:
        """Report should have title and metadata."""
        report = generate_report(
            sample_manifest, sample_results,
            {"model": "qwen", "target_path": "/project"},
            57.7,
        )
        assert "# DAIO Pipeline Report" in report
        assert "qwen" in report
        assert "57.7s" in report

    def test_report_contains_summary_table(self, sample_manifest: dict, sample_results: dict) -> None:
        """Report should have success/failure counts."""
        report = generate_report(
            sample_manifest, sample_results,
            {"model": "qwen", "target_path": "/project"},
            57.7,
        )
        assert "Succeeded" in report
        assert "Failed" in report

    def test_report_contains_function_results(self, sample_manifest: dict, sample_results: dict) -> None:
        """Report should list per-function results."""
        report = generate_report(
            sample_manifest, sample_results,
            {"model": "qwen", "target_path": "/project"},
            57.7,
        )
        assert "add" in report
        assert "multiply" in report

    def test_report_contains_failure_details(self, sample_manifest: dict, sample_results: dict) -> None:
        """Report should include error messages for failed functions."""
        report = generate_report(
            sample_manifest, sample_results,
            {"model": "qwen", "target_path": "/project"},
            57.7,
        )
        assert "Failed Functions" in report
        assert "SyntaxError" in report
        assert "LOC shrinkage" in report

    def test_report_timing_section(self, sample_manifest: dict, sample_results: dict) -> None:
        """Report should have timing breakdown."""
        report = generate_report(
            sample_manifest, sample_results,
            {"model": "qwen", "target_path": "/project"},
            57.7,
        )
        assert "Timing" in report
        assert "Average per function" in report


class TestSaveReport:
    """Tests for report file output."""

    def test_saves_markdown(self, tmp_path: Path) -> None:
        """Should write report to disk."""
        path = tmp_path / "report.md"
        save_report("# Test Report\n\nOK.", path)
        assert path.exists()
        assert "# Test Report" in path.read_text(encoding="utf-8")


class TestSaveResultsJSON:
    """Tests for JSON results output."""

    def test_round_trip(self, tmp_path: Path, sample_results: dict) -> None:
        """Saved JSON should be loadable and match original."""
        path = tmp_path / "results.json"
        save_results_json(sample_results, path)

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["aaa111bbb222"]["status"] == "SUCCESS"
        assert loaded["ccc333ddd444"]["status"] == "FAILED"


# ===================================================================
# Rollback Tests
# ===================================================================


class TestRollbackFunction:
    """Tests for per-function git revert."""

    def test_successful_revert(self) -> None:
        """Mocked successful git revert should return True."""
        import subprocess

        class MockResult:
            returncode = 0
            stdout = ""
            stderr = ""

        with patch("daio.audit.rollback.subprocess.run", return_value=MockResult()):
            result = rollback_function("abc1234")
        assert result is True

    def test_failed_revert(self) -> None:
        """Mocked failed git revert should return False."""
        class MockResult:
            returncode = 1
            stdout = ""
            stderr = "merge conflict"

        with patch("daio.audit.rollback.subprocess.run", return_value=MockResult()):
            result = rollback_function("abc1234")
        assert result is False


class TestRollbackAll:
    """Tests for full pipeline rollback."""

    def test_reverts_in_reverse_order(self, sample_results: dict) -> None:
        """Should revert commits in reverse order."""
        call_order: list[str] = []

        class MockResult:
            returncode = 0
            stdout = ""
            stderr = ""

        def mock_run(cmd, **kwargs):
            if "revert" in cmd:
                call_order.append(cmd[3])  # commit hash
            return MockResult()

        with patch("daio.audit.rollback.subprocess.run", side_effect=mock_run):
            results = rollback_all(sample_results)

        # Only SUCCESS entries with commit_hash should be reverted
        assert len(call_order) == 1
        assert "abc1234" in call_order


class TestStripAllAnchors:
    """Tests for anchor stripping from source files."""

    def test_strips_anchors_from_files(self, tmp_path: Path) -> None:
        """Should remove UID anchor comments from source files."""
        # Create a file with anchors
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "test.py").write_text(ANCHORED_SOURCE, encoding="utf-8")

        manifest = {
            "files": {
                "src/test.py": {"functions": []},
            },
        }

        modified = strip_all_anchors(manifest, tmp_path)
        assert modified == 1

        content = (src_dir / "test.py").read_text(encoding="utf-8")
        assert ":START" not in content
        assert ":END" not in content
        assert "def add" in content  # Function itself preserved

    def test_no_change_without_anchors(self, tmp_path: Path) -> None:
        """File without anchors should not be modified."""
        f = tmp_path / "clean.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")

        manifest = {"files": {"clean.py": {"functions": []}}}
        modified = strip_all_anchors(manifest, tmp_path)
        assert modified == 0
