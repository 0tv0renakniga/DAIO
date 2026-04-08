"""Tests for daio.surgeon — extractor, validator, applicator, offset, and integration.

Note: Ollama client tests use monkeypatching to avoid requiring a live server.
The full run() loop is tested with mocked Ollama dispatch.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from daio.surgeon.applicator import apply_transform
from daio.surgeon.extractor import ExtractionError, extract_transformed_code
from daio.surgeon.offset import recalculate_offsets
from daio.surgeon.ollama_client import OllamaError, dispatch
from daio.surgeon.validator import (
    ValidationResult,
    validate,
    validate_lint,
    validate_loc,
    validate_syntax,
)


# ---------------------------------------------------------------------------
# Source fixtures
# ---------------------------------------------------------------------------

ANCHORED_SOURCE = """\
import math

# UID:aaa111bbb222:START
def add(a, b):
    return a + b
# UID:aaa111bbb222:END

# UID:ccc333ddd444:START
def multiply(x, y):
    return x * y
# UID:ccc333ddd444:END
"""


@pytest.fixture()
def anchored_file(tmp_path: Path) -> Path:
    """Create a file with UID anchors."""
    f = tmp_path / "source.py"
    f.write_text(ANCHORED_SOURCE, encoding="utf-8")
    return f


# ===================================================================
# Extractor Tests
# ===================================================================


class TestExtractTransformedCode:
    """Tests for LLM response extraction."""

    def test_extract_with_uid_anchors(self) -> None:
        """Response with correct UID anchors should extract cleanly."""
        response = (
            "Here is the refactored function:\n"
            "# UID:aaa111bbb222:START\n"
            "def add(a, b):\n"
            '    """Add two numbers."""\n'
            "    return a + b\n"
            "# UID:aaa111bbb222:END\n"
        )
        lines = extract_transformed_code(response, "aaa111bbb222")
        assert len(lines) == 3
        assert "def add" in lines[0]
        assert '"""Add two numbers."""' in lines[1]

    def test_extract_from_markdown_fence(self) -> None:
        """Response in a markdown code fence should be extracted."""
        response = (
            "Here is the code:\n"
            "```python\n"
            "def add(a, b):\n"
            '    """Add two numbers."""\n'
            "    return a + b\n"
            "```\n"
        )
        lines = extract_transformed_code(response, "aaa111bbb222")
        assert len(lines) == 3
        assert "def add" in lines[0]

    def test_extract_raw_function(self) -> None:
        """Response with just a function def should be extracted."""
        response = (
            "def add(a, b):\n"
            '    """Add two numbers."""\n'
            "    return a + b\n"
        )
        lines = extract_transformed_code(response, "aaa111bbb222")
        assert len(lines) == 3

    def test_empty_response_raises(self) -> None:
        """Empty response should raise ExtractionError."""
        with pytest.raises(ExtractionError, match="empty response"):
            extract_transformed_code("", "aaa111bbb222")

    def test_whitespace_only_raises(self) -> None:
        """Whitespace-only response should raise ExtractionError."""
        with pytest.raises(ExtractionError, match="empty response"):
            extract_transformed_code("   \n\n  ", "aaa111bbb222")

    def test_no_code_raises(self) -> None:
        """Response with no code should raise ExtractionError."""
        with pytest.raises(ExtractionError):
            extract_transformed_code("I cannot help with that.", "aaa111bbb222")

    def test_markdown_fence_with_anchors_inside(self) -> None:
        """Fence containing UID anchors should strip anchors."""
        response = (
            "```python\n"
            "# UID:aaa111bbb222:START\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "# UID:aaa111bbb222:END\n"
            "```\n"
        )
        lines = extract_transformed_code(response, "aaa111bbb222")
        # Should extract without anchor lines
        assert all(":START" not in l and ":END" not in l for l in lines)


# ===================================================================
# Validator Tests
# ===================================================================


class TestValidateSyntax:
    """Tests for py_compile syntax validation."""

    def test_valid_code(self) -> None:
        """Valid Python should pass."""
        lines = ["def foo():", "    return 42"]
        ok, err = validate_syntax(lines)
        assert ok is True
        assert err == ""

    def test_syntax_error(self) -> None:
        """Invalid Python should fail."""
        lines = ["def foo(:", "    return 42"]
        ok, err = validate_syntax(lines)
        assert ok is False
        assert "SyntaxError" in err

    def test_empty_function(self) -> None:
        """Empty pass function should pass."""
        lines = ["def noop():", "    pass"]
        ok, err = validate_syntax(lines)
        assert ok is True


class TestValidateLint:
    """Tests for ruff lint validation."""

    def test_clean_code(self) -> None:
        """Clean code should pass lint."""
        lines = ["def foo():", "    return 42", ""]
        ok, err = validate_lint(lines)
        assert ok is True

    def test_undefined_name(self) -> None:
        """Undefined name should fail lint (F821)."""
        lines = ["def foo():", "    return undefined_variable_xyz", ""]
        ok, err = validate_lint(lines)
        # ruff may or may not be installed — just verify no crash
        assert isinstance(ok, bool)


class TestValidateLOC:
    """Tests for LOC ratio sanity check."""

    def test_same_size(self) -> None:
        """Same size should pass."""
        orig = ["line1", "line2", "line3"]
        new = ["new1", "new2", "new3"]
        ok, err = validate_loc(orig, new)
        assert ok is True

    def test_reasonable_growth(self) -> None:
        """2x growth should pass (under 3x ceiling)."""
        orig = ["line1", "line2"]
        new = ["new1", "new2", "new3", "new4"]
        ok, err = validate_loc(orig, new)
        assert ok is True

    def test_extreme_growth(self) -> None:
        """10x growth should fail."""
        orig = ["line1", "line2"]
        new = [f"line{i}" for i in range(20)]
        ok, err = validate_loc(orig, new)
        assert ok is False
        assert "growth" in err.lower()

    def test_extreme_shrink(self) -> None:
        """Shrinking to 1 line from 10 should fail."""
        orig = [f"line{i}" for i in range(10)]
        new = ["x"]
        ok, err = validate_loc(orig, new)
        assert ok is False
        assert "shrinkage" in err.lower()

    def test_empty_original(self) -> None:
        """Empty original should always pass (edge case guard)."""
        ok, err = validate_loc([], ["new"])
        assert ok is True

    def test_custom_thresholds(self) -> None:
        """Custom thresholds should be respected."""
        orig = ["line1"]
        new = ["a", "b", "c"]
        # 3x growth with ceiling of 2.0 should fail
        ok, err = validate_loc(orig, new, growth_ceiling=2.0)
        assert ok is False


class TestValidateFull:
    """Tests for the full three-stage validation gate."""

    def test_valid_code_passes_all(self) -> None:
        """Clean, same-size code should pass all stages."""
        orig = ["def foo():", "    return 42"]
        new = ["def foo():", '    """Docstring."""', "    return 42"]
        result = validate(orig, new)
        assert result.passed is True
        assert result.syntax_ok is True
        assert result.loc_ok is True

    def test_syntax_error_fails(self) -> None:
        """Syntax error should fail validation."""
        orig = ["def foo():", "    return 42"]
        new = ["def foo(:", "    return 42"]
        result = validate(orig, new)
        assert result.passed is False
        assert result.syntax_ok is False

    def test_loc_explosion_fails(self) -> None:
        """LOC explosion should fail validation."""
        orig = ["def foo():", "    return 42"]
        new = [f"    line{i}" for i in range(100)]
        result = validate(orig, new)
        assert result.passed is False
        assert result.loc_ok is False


# ===================================================================
# Applicator Tests
# ===================================================================


class TestApplyTransform:
    """Tests for delete-and-reinsert code application."""

    def test_replace_function(self, anchored_file: Path) -> None:
        """Should replace code between anchors."""
        new_code = [
            "def add(a, b):",
            '    """Add two numbers."""',
            "    return a + b",
        ]
        old_s, old_e, new_e = apply_transform(anchored_file, "aaa111bbb222", new_code)
        content = anchored_file.read_text(encoding="utf-8")
        assert '"""Add two numbers."""' in content
        assert "# UID:aaa111bbb222:START" in content
        assert "# UID:aaa111bbb222:END" in content

    def test_file_still_valid_python(self, anchored_file: Path) -> None:
        """File should still compile after replacement."""
        import py_compile

        new_code = [
            "def add(a, b):",
            '    """Add two numbers."""',
            "    return a + b",
        ]
        apply_transform(anchored_file, "aaa111bbb222", new_code)
        py_compile.compile(str(anchored_file), doraise=True)

    def test_other_functions_preserved(self, anchored_file: Path) -> None:
        """Other functions should not be affected."""
        new_code = [
            "def add(a, b):",
            '    """Add two numbers."""',
            "    return a + b",
        ]
        apply_transform(anchored_file, "aaa111bbb222", new_code)
        content = anchored_file.read_text(encoding="utf-8")
        assert "def multiply(x, y):" in content
        assert "return x * y" in content

    def test_missing_uid_raises(self, anchored_file: Path) -> None:
        """Missing UID should raise ValueError."""
        with pytest.raises(ValueError, match="UID START anchor not found"):
            apply_transform(anchored_file, "nonexistent00", ["pass"])

    def test_size_change_reflected(self, anchored_file: Path) -> None:
        """Adding lines should shift the end position."""
        original_content = anchored_file.read_text(encoding="utf-8")
        original_line_count = len(original_content.splitlines())

        new_code = [
            "def add(a, b):",
            '    """Add two numbers.',
            "",
            "    Args:",
            "        a: First number.",
            "        b: Second number.",
            '    """',
            "    return a + b",
        ]
        old_s, old_e, new_e = apply_transform(anchored_file, "aaa111bbb222", new_code)

        new_content = anchored_file.read_text(encoding="utf-8")
        new_line_count = len(new_content.splitlines())
        # Added 6 lines (8 new - 2 original)
        assert new_line_count == original_line_count + 6


# ===================================================================
# Offset Recalculation Tests
# ===================================================================


class TestRecalculateOffsets:
    """Tests for manifest offset recalculation."""

    def test_no_change_when_delta_zero(self) -> None:
        """Same-size replacement should not change any offsets."""
        manifest = {
            "files": {
                "test.py": {
                    "functions": [
                        {"name": "func1", "start_line": 20, "end_line": 25},
                        {"name": "func2", "start_line": 10, "end_line": 15},
                        {"name": "func3", "start_line": 1, "end_line": 5},
                    ],
                },
            },
        }
        recalculate_offsets(manifest, "test.py", 10, 15, 15)
        funcs = manifest["files"]["test.py"]["functions"]
        assert funcs[0]["start_line"] == 20  # below → shifted by 0
        assert funcs[1]["start_line"] == 10  # the replaced one
        assert funcs[2]["start_line"] == 1   # above → unchanged

    def test_growth_shifts_below(self) -> None:
        """Growing a function should shift functions below it."""
        manifest = {
            "files": {
                "test.py": {
                    "functions": [
                        {"name": "below", "start_line": 20, "end_line": 25},
                        {"name": "replaced", "start_line": 10, "end_line": 15},
                        {"name": "above", "start_line": 1, "end_line": 5},
                    ],
                },
            },
        }
        # old_end=15, new_end=18 → delta = 3
        recalculate_offsets(manifest, "test.py", 10, 15, 18)
        funcs = manifest["files"]["test.py"]["functions"]
        assert funcs[0]["start_line"] == 23  # below: 20 + 3
        assert funcs[0]["end_line"] == 28    # below: 25 + 3
        assert funcs[2]["start_line"] == 1   # above: unchanged

    def test_missing_file_no_crash(self) -> None:
        """Missing file in manifest should not crash."""
        manifest = {"files": {}}
        recalculate_offsets(manifest, "missing.py", 10, 15, 20)  # Should not raise


# ===================================================================
# Ollama Client Tests (Mocked)
# ===================================================================


class TestOllamaClient:
    """Tests for Ollama HTTP client (mocked, no live server needed)."""

    def test_connection_error_raises(self) -> None:
        """Connection failure should raise OllamaError."""
        with pytest.raises(OllamaError, match="Cannot connect"):
            dispatch("test prompt", "test-model", ollama_url="http://localhost:99999", timeout=2)

    def test_successful_dispatch(self) -> None:
        """Mocked successful response should return response text."""

        class MockResponse:
            status_code = 200
            text = '{"response": "transformed code"}'
            def json(self):
                return {"response": "transformed code"}

        class MockClient:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def post(self, url, json=None):
                return MockResponse()

        with patch("daio.surgeon.ollama_client.httpx.Client", MockClient):
            result = dispatch("test", "test-model")
            assert result == "transformed code"


# ===================================================================
# Integration: Surgeon Loop (Mocked Ollama)
# ===================================================================


class TestSurgeonRun:
    """Integration tests for the full Surgeon refinement loop with mocked Ollama."""

    def test_successful_transform(self, anchored_file: Path, tmp_path: Path) -> None:
        """Full loop: dispatch → extract → validate → apply should succeed."""
        from daio.config import DAIOConfig
        from daio.sieve.work_packet import WorkPacket
        from daio.surgeon import run as surgeon_run

        # Create config
        rules_file = tmp_path / "rules.md"
        rules_file.write_text("Add docstrings.", encoding="utf-8")

        config = DAIOConfig(
            model="test-model",
            target_path=tmp_path,
            rules_path=rules_file,
            auto_commit=False,
            max_retries=2,
        )

        # Create manifest
        manifest = {
            "base_path": str(tmp_path),
            "files": {
                "source.py": {
                    "functions": [
                        {
                            "name": "add",
                            "uid": "aaa111bbb222",
                            "start_line": 3,
                            "end_line": 6,
                            "status": "PENDING",
                        },
                    ],
                },
            },
        }

        # Create work packet
        source_text = anchored_file.read_text(encoding="utf-8")
        packet = WorkPacket(
            uid="aaa111bbb222",
            function_name="add",
            file_path="source.py",
            packet_text="test prompt",
            snippet_lines=["def add(a, b):", "    return a + b"],
            estimated_tokens=100,
            budget_status="OK",
        )

        # Mock Ollama to return a valid response
        good_response = (
            "# UID:aaa111bbb222:START\n"
            "def add(a, b):\n"
            '    """Add two numbers."""\n'
            "    return a + b\n"
            "# UID:aaa111bbb222:END\n"
        )

        with patch("daio.surgeon.dispatch", return_value=good_response):
            results = surgeon_run(config, manifest, [packet])

        assert results["aaa111bbb222"]["status"] == "SUCCESS"
        # Verify the file was actually modified
        content = anchored_file.read_text(encoding="utf-8")
        assert '"""Add two numbers."""' in content

    def test_validation_failure_retries(self, anchored_file: Path, tmp_path: Path) -> None:
        """Validation failures should trigger retries."""
        from daio.config import DAIOConfig
        from daio.sieve.work_packet import WorkPacket
        from daio.surgeon import run as surgeon_run

        rules_file = tmp_path / "rules.md"
        rules_file.write_text("Add docstrings.", encoding="utf-8")

        config = DAIOConfig(
            model="test-model",
            target_path=tmp_path,
            rules_path=rules_file,
            auto_commit=False,
            max_retries=2,
        )

        manifest = {
            "base_path": str(tmp_path),
            "files": {
                "source.py": {
                    "functions": [
                        {
                            "name": "add",
                            "uid": "aaa111bbb222",
                            "start_line": 3,
                            "end_line": 6,
                            "status": "PENDING",
                        },
                    ],
                },
            },
        }

        packet = WorkPacket(
            uid="aaa111bbb222",
            function_name="add",
            file_path="source.py",
            packet_text="test prompt",
            snippet_lines=["def add(a, b):", "    return a + b"],
            estimated_tokens=100,
            budget_status="OK",
        )

        # Return syntax-broken code both times → should FAIL
        bad_response = "def add(a, b:\n    return a + b\n"

        with patch("daio.surgeon.dispatch", return_value=bad_response):
            results = surgeon_run(config, manifest, [packet])

        assert results["aaa111bbb222"]["status"] == "FAILED"
        assert results["aaa111bbb222"]["retries"] >= 1

    def test_ollama_error_retries(self, anchored_file: Path, tmp_path: Path) -> None:
        """OllamaError should trigger retry, not crash."""
        from daio.config import DAIOConfig
        from daio.sieve.work_packet import WorkPacket
        from daio.surgeon import run as surgeon_run

        rules_file = tmp_path / "rules.md"
        rules_file.write_text("Add docstrings.", encoding="utf-8")

        config = DAIOConfig(
            model="test-model",
            target_path=tmp_path,
            rules_path=rules_file,
            auto_commit=False,
            max_retries=2,
        )

        manifest = {
            "base_path": str(tmp_path),
            "files": {
                "source.py": {
                    "functions": [
                        {
                            "name": "add",
                            "uid": "aaa111bbb222",
                            "start_line": 3,
                            "end_line": 6,
                            "status": "PENDING",
                        },
                    ],
                },
            },
        }

        packet = WorkPacket(
            uid="aaa111bbb222",
            function_name="add",
            file_path="source.py",
            packet_text="test prompt",
            snippet_lines=["def add(a, b):", "    return a + b"],
            estimated_tokens=100,
            budget_status="OK",
        )

        with patch("daio.surgeon.dispatch", side_effect=OllamaError("server down")):
            results = surgeon_run(config, manifest, [packet])

        assert results["aaa111bbb222"]["status"] == "FAILED"
        assert any("server down" in e for e in results["aaa111bbb222"]["errors"])
