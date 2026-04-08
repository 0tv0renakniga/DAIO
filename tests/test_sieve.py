"""Tests for daio.sieve — snippet extraction, header filtering, token counting, work packets."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from daio.cartographer.anchor import inject_anchors
from daio.cartographer.ast_walker import analyze_file
from daio.cartographer.uid import assign_uids
from daio.sieve.header import (
    build_global_header,
    collect_constants,
    collect_file_imports,
    extract_identifiers_from_snippet,
    filter_constants,
    filter_imports,
)
from daio.sieve.snippet import extract_by_line_range, extract_by_uid, find_all_uids
from daio.sieve.token_counter import check_budget, estimate_tokens
from daio.sieve.work_packet import WorkPacket, assemble_work_packet, save_work_packet


# ---------------------------------------------------------------------------
# Source fixtures
# ---------------------------------------------------------------------------

SOURCE_WITH_IMPORTS = '''\
import math
import os
from typing import Optional
from collections import defaultdict

MAX_SIZE = 1024
DEFAULT_NAME = "test"

def compute_distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

def list_files(directory):
    return os.listdir(directory)

def unused_function():
    pass
'''

ANCHORED_SOURCE = '''\
import math

# UID:aaa111bbb222:START
def add(a, b):
    return a + b
# UID:aaa111bbb222:END

# UID:ccc333ddd444:START
def distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
# UID:ccc333ddd444:END
'''


@pytest.fixture()
def anchored_file(tmp_path: Path) -> Path:
    """Create a file with pre-existing UID anchors."""
    f = tmp_path / "anchored.py"
    f.write_text(ANCHORED_SOURCE, encoding="utf-8")
    return f


@pytest.fixture()
def imports_file(tmp_path: Path) -> Path:
    """Create a file with multiple imports and constants."""
    f = tmp_path / "imports_mod.py"
    f.write_text(SOURCE_WITH_IMPORTS, encoding="utf-8")
    return f


@pytest.fixture()
def real_anchored_file(tmp_path: Path) -> Path:
    """Create a file, analyze it, inject real UID anchors."""
    source = '''\
import math
import os

def compute(x, y):
    return math.sqrt(x ** 2 + y ** 2)

def greet(name):
    return f"Hello, {name}"

def list_dir(path):
    return os.listdir(path)
'''
    f = tmp_path / "real.py"
    f.write_text(source, encoding="utf-8")
    analysis = analyze_file(f)
    uid_map = assign_uids(analysis.functions, f, tmp_path)
    inject_anchors(f, analysis.functions, uid_map)
    return f


# ===================================================================
# Snippet Extraction Tests
# ===================================================================


class TestExtractByUID:
    """Tests for UID-anchor-based snippet extraction."""

    def test_extract_simple_function(self) -> None:
        """Extract the 'add' function between anchors."""
        lines = ANCHORED_SOURCE.splitlines()
        snippet, start, end = extract_by_uid(lines, "aaa111bbb222")
        assert len(snippet) == 2
        assert "def add(a, b):" in snippet[0]
        assert "return a + b" in snippet[1]

    def test_extract_second_function(self) -> None:
        """Extract the 'distance' function between anchors."""
        lines = ANCHORED_SOURCE.splitlines()
        snippet, start, end = extract_by_uid(lines, "ccc333ddd444")
        assert len(snippet) == 2
        assert "def distance" in snippet[0]

    def test_missing_uid_raises(self) -> None:
        """Nonexistent UID should raise ValueError."""
        lines = ANCHORED_SOURCE.splitlines()
        with pytest.raises(ValueError, match="UID START anchor not found"):
            extract_by_uid(lines, "nonexistent00")

    def test_missing_end_anchor_raises(self) -> None:
        """START without END should raise ValueError."""
        lines = [
            "# UID:aaa111bbb222:START",
            "def broken():",
            "    pass",
            # No END anchor
        ]
        with pytest.raises(ValueError, match="UID END anchor not found"):
            extract_by_uid(lines, "aaa111bbb222")


class TestExtractByLineRange:
    """Tests for line-range-based extraction."""

    def test_valid_range(self) -> None:
        """Extract lines 2-4 from a 5-line source."""
        lines = ["line1", "line2", "line3", "line4", "line5"]
        result = extract_by_line_range(lines, 2, 4)
        assert result == ["line2", "line3", "line4"]

    def test_single_line(self) -> None:
        """Extract a single line."""
        lines = ["a", "b", "c"]
        result = extract_by_line_range(lines, 2, 2)
        assert result == ["b"]

    def test_out_of_bounds_raises(self) -> None:
        """End line beyond file length should raise."""
        lines = ["a", "b"]
        with pytest.raises(ValueError, match="exceeds file length"):
            extract_by_line_range(lines, 1, 5)

    def test_inverted_range_raises(self) -> None:
        """start > end should raise."""
        lines = ["a", "b", "c"]
        with pytest.raises(ValueError, match="start_line .* > end_line"):
            extract_by_line_range(lines, 3, 1)

    def test_zero_start_raises(self) -> None:
        """Start line 0 should raise (1-indexed)."""
        with pytest.raises(ValueError, match="start_line must be >= 1"):
            extract_by_line_range(["a"], 0, 1)


class TestFindAllUIDs:
    """Tests for UID discovery in source."""

    def test_finds_all_uids(self) -> None:
        """Should find both UIDs in anchored source."""
        lines = ANCHORED_SOURCE.splitlines()
        uids = find_all_uids(lines)
        assert uids == ["aaa111bbb222", "ccc333ddd444"]

    def test_empty_source(self) -> None:
        """Empty source should return empty list."""
        assert find_all_uids([]) == []


# ===================================================================
# Header / Import Filter Tests
# ===================================================================


class TestCollectFileImports:
    """Tests for import statement collection."""

    def test_collects_all_imports(self) -> None:
        """Should find all 4 import statements."""
        imports = collect_file_imports(SOURCE_WITH_IMPORTS)
        assert len(imports) == 4
        assert any("import math" in i for i in imports)
        assert any("from typing" in i for i in imports)

    def test_syntax_error_returns_empty(self) -> None:
        """Unparseable source should return empty list."""
        imports = collect_file_imports("def broken(\n")
        assert imports == []


class TestCollectConstants:
    """Tests for constant extraction."""

    def test_finds_uppercase_constants(self) -> None:
        """Should find MAX_SIZE and DEFAULT_NAME."""
        constants = collect_constants(SOURCE_WITH_IMPORTS)
        assert len(constants) == 2
        assert any("MAX_SIZE" in c for c in constants)
        assert any("DEFAULT_NAME" in c for c in constants)


class TestExtractIdentifiers:
    """Tests for identifier extraction from snippet."""

    def test_extracts_names(self) -> None:
        """Should extract function names and module references."""
        snippet = ["def compute(x, y):", "    return math.sqrt(x ** 2 + y ** 2)"]
        ids = extract_identifiers_from_snippet(snippet)
        assert "math" in ids
        assert "sqrt" in ids
        assert "x" in ids

    def test_handles_syntax_error_gracefully(self) -> None:
        """Unparseable snippet should fall back to regex extraction."""
        snippet = ["self.value += x  # method body without class"]
        ids = extract_identifiers_from_snippet(snippet)
        assert "self" in ids or "value" in ids


class TestFilterImports:
    """Tests for import relevance filtering."""

    def test_keeps_only_referenced(self) -> None:
        """Only imports providing referenced names should be kept."""
        imports = ["import math", "import os", "from typing import Optional"]
        ids = {"math", "sqrt", "x", "y"}
        filtered = filter_imports(imports, ids)
        assert len(filtered) == 1
        assert "import math" in filtered[0]

    def test_star_import_always_kept(self) -> None:
        """Star imports should always be kept (conservative)."""
        imports = ["from utils import *"]
        ids = {"anything"}
        filtered = filter_imports(imports, ids)
        assert len(filtered) == 1

    def test_aliased_import(self) -> None:
        """Alias should be matched, not original name."""
        imports = ["import numpy as np"]
        ids = {"np"}
        filtered = filter_imports(imports, ids)
        assert len(filtered) == 1


class TestFilterConstants:
    """Tests for constant relevance filtering."""

    def test_keeps_referenced_constants(self) -> None:
        """Only referenced constants should be kept."""
        constants = ['MAX_SIZE = 1024', 'DEFAULT_NAME = "test"']
        ids = {"MAX_SIZE", "some_var"}
        filtered = filter_constants(constants, ids)
        assert len(filtered) == 1
        assert "MAX_SIZE" in filtered[0]


class TestBuildGlobalHeader:
    """Tests for the full header builder."""

    def test_header_contains_only_relevant(self) -> None:
        """Header for distance function should include math, not os."""
        snippet = ["def compute_distance(x1, y1, x2, y2):",
                    "    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)"]
        header = build_global_header(SOURCE_WITH_IMPORTS, snippet)
        assert "import math" in header
        assert "import os" not in header

    def test_header_budget_truncation(self) -> None:
        """Very small budget should trigger truncation."""
        snippet = ["def compute_distance(x1, y1, x2, y2):",
                    "    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)"]
        header = build_global_header(SOURCE_WITH_IMPORTS, snippet, header_token_budget=1)
        assert "TRUNCATED" in header or len(header) < 50


# ===================================================================
# Token Counter Tests
# ===================================================================


class TestEstimateTokens:
    """Tests for token estimation."""

    def test_empty_string(self) -> None:
        """Empty string should be 0 tokens."""
        assert estimate_tokens("") == 0

    def test_short_string(self) -> None:
        """Short string should be at least 1 token."""
        assert estimate_tokens("hi") >= 1

    def test_predictable_estimate(self) -> None:
        """400-char string should be ~100 tokens."""
        text = "x" * 400
        assert estimate_tokens(text) == 100


class TestCheckBudget:
    """Tests for budget status checking."""

    def test_under_budget_ok(self) -> None:
        assert check_budget(3000, 4096) == "OK"

    def test_at_budget_ok(self) -> None:
        assert check_budget(4096, 4096) == "OK"

    def test_over_budget_warn(self) -> None:
        assert check_budget(5000, 4096) == "WARN"

    def test_double_budget_abort(self) -> None:
        assert check_budget(8192, 4096) == "ABORT"

    def test_triple_budget_abort(self) -> None:
        assert check_budget(15000, 4096) == "ABORT"


# ===================================================================
# Work Packet Tests
# ===================================================================


class TestAssembleWorkPacket:
    """Tests for work packet assembly."""

    def test_packet_contains_all_sections(self) -> None:
        """Work packet should contain RULES, GLOBAL CONTEXT, TARGET FUNCTION, INSTRUCTION."""
        lines = ANCHORED_SOURCE.splitlines()
        packet = assemble_work_packet(
            source_text=ANCHORED_SOURCE,
            source_lines=lines,
            uid="aaa111bbb222",
            function_name="add",
            file_path="test.py",
            rules_text="Add docstrings.",
            token_budget=4096,
        )
        assert "=== RULES ===" in packet.packet_text
        assert "Add docstrings." in packet.packet_text
        assert "=== GLOBAL CONTEXT ===" in packet.packet_text
        assert "=== TARGET FUNCTION ===" in packet.packet_text
        assert "UID:aaa111bbb222:START" in packet.packet_text
        assert "UID:aaa111bbb222:END" in packet.packet_text
        assert "=== INSTRUCTION ===" in packet.packet_text

    def test_packet_metadata(self) -> None:
        """WorkPacket dataclass should have correct metadata."""
        lines = ANCHORED_SOURCE.splitlines()
        packet = assemble_work_packet(
            source_text=ANCHORED_SOURCE,
            source_lines=lines,
            uid="aaa111bbb222",
            function_name="add",
            file_path="test.py",
            rules_text="Add docstrings.",
        )
        assert packet.uid == "aaa111bbb222"
        assert packet.function_name == "add"
        assert packet.file_path == "test.py"
        assert packet.estimated_tokens > 0
        assert packet.budget_status == "OK"

    def test_snippet_lines_preserved(self) -> None:
        """snippet_lines should contain the raw extracted function lines."""
        lines = ANCHORED_SOURCE.splitlines()
        packet = assemble_work_packet(
            source_text=ANCHORED_SOURCE,
            source_lines=lines,
            uid="aaa111bbb222",
            function_name="add",
            file_path="test.py",
            rules_text="Add docstrings.",
        )
        assert len(packet.snippet_lines) == 2
        assert "def add" in packet.snippet_lines[0]

    def test_missing_uid_raises(self) -> None:
        """Nonexistent UID should propagate ValueError."""
        lines = ANCHORED_SOURCE.splitlines()
        with pytest.raises(ValueError):
            assemble_work_packet(
                source_text=ANCHORED_SOURCE,
                source_lines=lines,
                uid="nonexistent00",
                function_name="missing",
                file_path="test.py",
                rules_text="Rules.",
            )

    def test_token_counter_backend_propagates(self) -> None:
        """assemble_work_packet should pass token backend to estimator."""
        lines = ANCHORED_SOURCE.splitlines()
        with patch("daio.sieve.work_packet.estimate_tokens", return_value=123) as mock_estimate:
            packet = assemble_work_packet(
                source_text=ANCHORED_SOURCE,
                source_lines=lines,
                uid="aaa111bbb222",
                function_name="add",
                file_path="test.py",
                rules_text="Add docstrings.",
                token_counter_backend="heuristic",
            )
        assert packet.estimated_tokens == 123
        mock_estimate.assert_called_once()
        _, kwargs = mock_estimate.call_args
        assert kwargs["backend"] == "heuristic"


class TestSaveWorkPacket:
    """Tests for work packet persistence."""

    def test_saves_to_disk(self, tmp_path: Path) -> None:
        """Saved work packet should be readable from disk."""
        packet = WorkPacket(
            uid="aaa111bbb222",
            function_name="test_func",
            file_path="test.py",
            packet_text="test content",
            snippet_lines=["def test_func():", "    pass"],
            estimated_tokens=10,
            budget_status="OK",
        )
        path = save_work_packet(packet, tmp_path)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "test content"
        assert "aaa111bbb222" in path.name

    def test_creates_directory(self, tmp_path: Path) -> None:
        """Should create output directory if it doesn't exist."""
        nested = tmp_path / "deep" / "nested"
        packet = WorkPacket(
            uid="aaa111bbb222",
            function_name="f",
            file_path="t.py",
            packet_text="content",
            snippet_lines=[],
            estimated_tokens=1,
            budget_status="OK",
        )
        path = save_work_packet(packet, nested)
        assert path.exists()


class TestRealAnchoredWorkPackets:
    """Integration tests using real AST analysis + anchor injection + work packet assembly."""

    def test_end_to_end_packet_for_real_file(self, real_anchored_file: Path) -> None:
        """Full pipeline: analyze → inject anchors → extract → assemble packet."""
        source_text = real_anchored_file.read_text(encoding="utf-8")
        source_lines = source_text.splitlines()
        uids = find_all_uids(source_lines)
        assert len(uids) >= 1  # at least one function

        # Assemble a packet for the first function
        uid = uids[0]
        packet = assemble_work_packet(
            source_text=source_text,
            source_lines=source_lines,
            uid=uid,
            function_name="test_func",
            file_path="real.py",
            rules_text="Add Google-style docstrings.",
        )
        assert packet.budget_status == "OK"
        assert "=== TARGET FUNCTION ===" in packet.packet_text
        assert f"UID:{uid}:START" in packet.packet_text
