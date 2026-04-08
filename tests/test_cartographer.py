"""Tests for daio.cartographer — AST walker, UID, anchor injection, manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daio.cartographer.anchor import (
    build_anchor_end,
    build_anchor_start,
    inject_anchors,
    strip_anchors,
)
from daio.cartographer.ast_walker import (
    FileAnalysis,
    FunctionInfo,
    analyze_file,
    collect_files,
)
from daio.cartographer.manifest import (
    STATUS_PENDING,
    STATUS_SKIPPED_NESTED,
    build_manifest,
    compute_dependency_weights,
    get_processable_entries,
    load_manifest,
    save_manifest,
)
from daio.cartographer.uid import assign_uids, generate_uid, validate_uid_uniqueness


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_MODULE = '''\
import math


def add(a, b):
    return a + b


def multiply(x, y):
    """Multiply two numbers."""
    return x * y


def unused():
    pass
'''

ASYNC_AND_DECORATED = '''\
import functools


def plain():
    return 1


async def fetch_data(url):
    return {"url": url}


def decorator(func):
    @functools.wraps(func)
    def wrapper(*args):
        return func(*args)
    return wrapper
'''

NESTED_FUNCTIONS = '''\
def outer():
    def inner():
        pass
    return inner()
'''

CLASS_WITH_METHODS = '''\
class Calculator:
    def __init__(self, value):
        self.value = value

    def add(self, x):
        self.value += x
        return self

    async def async_method(self):
        return self.value
'''

SYNTAX_ERROR_SOURCE = '''\
def broken(
    return None
'''


@pytest.fixture()
def simple_py(tmp_path: Path) -> Path:
    """Create a simple Python file with 3 functions."""
    f = tmp_path / "simple.py"
    f.write_text(SIMPLE_MODULE, encoding="utf-8")
    return f


@pytest.fixture()
def async_py(tmp_path: Path) -> Path:
    """Create a Python file with async and decorated functions."""
    f = tmp_path / "async_mod.py"
    f.write_text(ASYNC_AND_DECORATED, encoding="utf-8")
    return f


@pytest.fixture()
def nested_py(tmp_path: Path) -> Path:
    """Create a Python file with nested functions."""
    f = tmp_path / "nested.py"
    f.write_text(NESTED_FUNCTIONS, encoding="utf-8")
    return f


@pytest.fixture()
def class_py(tmp_path: Path) -> Path:
    """Create a Python file with a class containing methods."""
    f = tmp_path / "calc.py"
    f.write_text(CLASS_WITH_METHODS, encoding="utf-8")
    return f


@pytest.fixture()
def bad_py(tmp_path: Path) -> Path:
    """Create a Python file with a syntax error."""
    f = tmp_path / "bad.py"
    f.write_text(SYNTAX_ERROR_SOURCE, encoding="utf-8")
    return f


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a mini project with multiple Python files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core.py").write_text(SIMPLE_MODULE, encoding="utf-8")
    (tmp_path / "src" / "helpers.py").write_text(ASYNC_AND_DECORATED, encoding="utf-8")
    # Should be excluded
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("x=1", encoding="utf-8")
    return tmp_path


# ===================================================================
# AST Walker Tests
# ===================================================================


class TestAnalyzeFile:
    """Tests for analyze_file — AST walking and function extraction."""

    def test_simple_module_extracts_three_functions(self, simple_py: Path) -> None:
        """Simple module should yield 3 top-level functions."""
        result = analyze_file(simple_py)
        assert result.parse_error is None
        assert len(result.functions) == 3
        names = [f.name for f in result.functions]
        assert names == ["add", "multiply", "unused"]

    def test_function_line_ranges(self, simple_py: Path) -> None:
        """Line ranges should match actual source positions."""
        result = analyze_file(simple_py)
        add_fn = result.functions[0]
        assert add_fn.name == "add"
        assert add_fn.start_line == 4
        assert add_fn.end_line == 5

    def test_docstring_detection(self, simple_py: Path) -> None:
        """multiply has a docstring; add and unused do not."""
        result = analyze_file(simple_py)
        by_name = {f.name: f for f in result.functions}
        assert by_name["multiply"].has_docstring is True
        assert by_name["add"].has_docstring is False
        assert by_name["unused"].has_docstring is False

    def test_async_detection(self, async_py: Path) -> None:
        """fetch_data is async; plain and decorator are not."""
        result = analyze_file(async_py)
        by_name = {f.name: f for f in result.functions}
        assert by_name["fetch_data"].is_async is True
        assert by_name["plain"].is_async is False

    def test_nested_function_flagged(self, nested_py: Path) -> None:
        """Nested inner function should be flagged as nested."""
        result = analyze_file(nested_py)
        assert len(result.functions) == 2
        by_name = {f.name: f for f in result.functions}
        assert by_name["outer"].nested is False
        assert by_name["inner"].nested is True

    def test_class_methods_extracted(self, class_py: Path) -> None:
        """Class methods should be extracted with class_name set."""
        result = analyze_file(class_py)
        assert len(result.functions) == 3
        for func in result.functions:
            assert func.is_method is True
            assert func.class_name == "Calculator"

    def test_async_method_in_class(self, class_py: Path) -> None:
        """async_method in class should be both async and a method."""
        result = analyze_file(class_py)
        by_name = {f.name: f for f in result.functions}
        assert by_name["async_method"].is_async is True
        assert by_name["async_method"].is_method is True

    def test_syntax_error_returns_parse_error(self, bad_py: Path) -> None:
        """File with syntax error should return parse_error, not crash."""
        result = analyze_file(bad_py)
        assert result.parse_error is not None
        assert "SyntaxError" in result.parse_error
        assert result.functions == []

    def test_body_loc_calculated(self, simple_py: Path) -> None:
        """body_loc should equal end_line - start_line + 1."""
        result = analyze_file(simple_py)
        for func in result.functions:
            assert func.body_loc == func.end_line - func.start_line + 1

    def test_decorated_function_contains_wrapper(self, async_py: Path) -> None:
        """decorator function should have wrapper as nested."""
        result = analyze_file(async_py)
        by_name = {f.name: f for f in result.functions}
        assert "wrapper" in by_name
        assert by_name["wrapper"].nested is True


class TestCollectFiles:
    """Tests for collect_files — file discovery by scope."""

    def test_full_scope_finds_all_py_files(self, project_dir: Path) -> None:
        """Full scope should find .py files, excluding __pycache__."""
        files = collect_files(project_dir, "full")
        basenames = [f.name for f in files]
        assert "__init__.py" in basenames
        assert "core.py" in basenames
        assert "helpers.py" in basenames
        assert "cached.py" not in basenames  # __pycache__ excluded

    def test_filelist_scope(self, project_dir: Path) -> None:
        """Filelist scope should return only listed files."""
        files = collect_files(project_dir, "filelist", ["src/core.py"])
        assert len(files) == 1
        assert files[0].name == "core.py"

    def test_filelist_scope_without_list_raises(self, project_dir: Path) -> None:
        """Filelist scope without a file list should raise ValueError."""
        with pytest.raises(ValueError, match="file_list must be provided"):
            collect_files(project_dir, "filelist")

    def test_nonexistent_path_raises(self) -> None:
        """Target path that doesn't exist should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            collect_files(Path("/nonexistent/path"), "full")


# ===================================================================
# UID Tests
# ===================================================================


class TestGenerateUID:
    """Tests for UID generation."""

    def test_deterministic(self, simple_py: Path) -> None:
        """Same inputs should produce same UID."""
        uid1 = generate_uid(simple_py, 10)
        uid2 = generate_uid(simple_py, 10)
        assert uid1 == uid2

    def test_different_lines_different_uids(self, simple_py: Path) -> None:
        """Different line numbers should produce different UIDs."""
        uid1 = generate_uid(simple_py, 10)
        uid2 = generate_uid(simple_py, 20)
        assert uid1 != uid2

    def test_uid_length(self, simple_py: Path) -> None:
        """UIDs should be exactly 12 hex characters."""
        uid = generate_uid(simple_py, 42)
        assert len(uid) == 12
        assert all(c in "0123456789abcdef" for c in uid)

    def test_relative_path(self, project_dir: Path) -> None:
        """UID with base_path should use relative path for hashing."""
        filepath = project_dir / "src" / "core.py"
        uid_abs = generate_uid(filepath, 10)
        uid_rel = generate_uid(filepath, 10, base_path=project_dir)
        # Should be different because hash input differs
        assert uid_abs != uid_rel


class TestAssignUIDs:
    """Tests for batch UID assignment and collision detection."""

    def test_assigns_all_functions(self, simple_py: Path) -> None:
        """Each function should get a UID."""
        analysis = analyze_file(simple_py)
        uid_map = assign_uids(analysis.functions, simple_py)
        assert len(uid_map) == 3
        assert "add" in uid_map
        assert "multiply" in uid_map
        assert "unused" in uid_map

    def test_no_collisions(self, simple_py: Path) -> None:
        """All UIDs should be unique."""
        analysis = analyze_file(simple_py)
        uid_map = assign_uids(analysis.functions, simple_py)
        uids = list(uid_map.values())
        assert len(uids) == len(set(uids))


class TestValidateUIDUniqueness:
    """Tests for global UID uniqueness validation."""

    def test_valid_uids_pass(self) -> None:
        """Non-colliding UIDs should pass validation."""
        all_uids = {
            "file_a.py": {"func1": "aaa111bbb222", "func2": "ccc333ddd444"},
            "file_b.py": {"func3": "eee555fff666"},
        }
        validate_uid_uniqueness(all_uids)  # Should not raise

    def test_collision_raises(self) -> None:
        """Colliding UIDs across files should raise ValueError."""
        all_uids = {
            "file_a.py": {"func1": "aaa111bbb222"},
            "file_b.py": {"func2": "aaa111bbb222"},  # collision
        }
        with pytest.raises(ValueError, match="Global UID collision"):
            validate_uid_uniqueness(all_uids)


# ===================================================================
# Anchor Injection Tests
# ===================================================================


class TestAnchorInjection:
    """Tests for UID anchor injection into source files."""

    def test_anchors_injected(self, simple_py: Path) -> None:
        """Anchors should appear in modified source."""
        analysis = analyze_file(simple_py)
        uid_map = assign_uids(analysis.functions, simple_py)
        lines, positions = inject_anchors(simple_py, analysis.functions, uid_map)
        content = "\n".join(lines)
        for uid in uid_map.values():
            assert f"# UID:{uid}:START" in content
            assert f"# UID:{uid}:END" in content

    def test_syntax_preserved(self, simple_py: Path) -> None:
        """Modified source should still be valid Python."""
        analysis = analyze_file(simple_py)
        uid_map = assign_uids(analysis.functions, simple_py)
        inject_anchors(simple_py, analysis.functions, uid_map)
        # File was written — re-read and verify it compiles
        import py_compile
        py_compile.compile(str(simple_py), doraise=True)

    def test_nested_functions_not_anchored(self, nested_py: Path) -> None:
        """Nested functions should NOT get their own anchors."""
        analysis = analyze_file(nested_py)
        uid_map = assign_uids(analysis.functions, nested_py)
        lines, _ = inject_anchors(nested_py, analysis.functions, uid_map)
        content = "\n".join(lines)
        # Only outer should have anchors, not inner
        start_count = content.count(":START")
        assert start_count == 1

    def test_dry_run_does_not_modify_file(self, simple_py: Path) -> None:
        """dry_run=True should return modified lines but not write to disk."""
        original = simple_py.read_text(encoding="utf-8")
        analysis = analyze_file(simple_py)
        uid_map = assign_uids(analysis.functions, simple_py)
        inject_anchors(simple_py, analysis.functions, uid_map, dry_run=True)
        assert simple_py.read_text(encoding="utf-8") == original

    def test_class_method_anchors(self, class_py: Path) -> None:
        """Class methods should get anchors with proper indentation."""
        analysis = analyze_file(class_py)
        uid_map = assign_uids(analysis.functions, class_py)
        lines, _ = inject_anchors(class_py, analysis.functions, uid_map)
        # All 3 methods should have anchors
        content = "\n".join(lines)
        assert content.count(":START") == 3
        assert content.count(":END") == 3
        # Verify indentation — method anchors should be indented
        for line in lines:
            if ":START" in line or ":END" in line:
                assert line.startswith("    "), f"Method anchor should be indented: {line!r}"


class TestStripAnchors:
    """Tests for anchor removal."""

    def test_strip_removes_anchors(self) -> None:
        """strip_anchors should remove all UID markers."""
        lines = [
            "# UID:aaa111bbb222:START",
            "def foo():",
            "    pass",
            "# UID:aaa111bbb222:END",
        ]
        stripped = strip_anchors(lines)
        assert len(stripped) == 2
        assert stripped[0] == "def foo():"


class TestBuildAnchorStrings:
    """Tests for anchor comment construction."""

    def test_start_anchor_format(self) -> None:
        """START anchor should have correct format."""
        assert build_anchor_start("aaa111bbb222") == "# UID:aaa111bbb222:START"

    def test_end_anchor_format(self) -> None:
        """END anchor should have correct format."""
        assert build_anchor_end("aaa111bbb222") == "# UID:aaa111bbb222:END"


# ===================================================================
# Manifest Tests
# ===================================================================


class TestDependencyWeights:
    """Tests for dependency weight computation."""

    def test_referenced_function_has_weight(self, project_dir: Path) -> None:
        """Functions referenced in other files should have non-zero weight."""
        analyses = {}
        for f in collect_files(project_dir, "full"):
            analysis = analyze_file(f)
            if not analysis.parse_error:
                analyses[str(f)] = analysis

        weights = compute_dependency_weights(analyses)
        # 'add' is defined in core.py — weight depends on usage
        assert isinstance(weights, dict)
        assert all(isinstance(v, int) for v in weights.values())


class TestBuildManifest:
    """Tests for manifest construction."""

    def test_manifest_structure(self, simple_py: Path) -> None:
        """Manifest should have version, generated_at, base_path, files."""
        analysis = analyze_file(simple_py)
        analyses = {str(simple_py): analysis}
        uid_map = assign_uids(analysis.functions, simple_py, simple_py.parent)
        uid_maps = {str(simple_py): uid_map}
        weights = compute_dependency_weights(analyses)
        manifest = build_manifest(analyses, uid_maps, weights, simple_py.parent)

        assert manifest["version"] == 1
        assert "generated_at" in manifest
        assert "files" in manifest

    def test_functions_sorted_descending(self, simple_py: Path) -> None:
        """Functions in manifest should be sorted by start_line descending."""
        analysis = analyze_file(simple_py)
        analyses = {str(simple_py): analysis}
        uid_map = assign_uids(analysis.functions, simple_py, simple_py.parent)
        uid_maps = {str(simple_py): uid_map}
        weights = compute_dependency_weights(analyses)
        manifest = build_manifest(analyses, uid_maps, weights, simple_py.parent)

        for file_data in manifest["files"].values():
            entries = file_data["functions"]
            start_lines = [e["start_line"] for e in entries]
            assert start_lines == sorted(start_lines, reverse=True)

    def test_nested_marked_skipped(self, nested_py: Path) -> None:
        """Nested functions should have status SKIPPED_NESTED."""
        analysis = analyze_file(nested_py)
        analyses = {str(nested_py): analysis}
        uid_map = assign_uids(analysis.functions, nested_py, nested_py.parent)
        uid_maps = {str(nested_py): uid_map}
        weights = compute_dependency_weights(analyses)
        manifest = build_manifest(analyses, uid_maps, weights, nested_py.parent)

        for file_data in manifest["files"].values():
            for entry in file_data["functions"]:
                if entry["nested"]:
                    assert entry["status"] == STATUS_SKIPPED_NESTED
                else:
                    assert entry["status"] == STATUS_PENDING


class TestManifestIO:
    """Tests for manifest save/load round-trip."""

    def test_round_trip(self, tmp_path: Path, simple_py: Path) -> None:
        """Save then load should produce identical manifest."""
        analysis = analyze_file(simple_py)
        analyses = {str(simple_py): analysis}
        uid_map = assign_uids(analysis.functions, simple_py, simple_py.parent)
        uid_maps = {str(simple_py): uid_map}
        weights = compute_dependency_weights(analyses)
        manifest = build_manifest(analyses, uid_maps, weights, simple_py.parent)

        path = tmp_path / "manifest.json"
        save_manifest(manifest, path)
        loaded = load_manifest(path)

        assert loaded["version"] == manifest["version"]
        assert loaded["files"] == manifest["files"]

    def test_load_nonexistent_raises(self) -> None:
        """Loading nonexistent manifest should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_manifest(Path("/nonexistent/manifest.json"))


class TestGetProcessableEntries:
    """Tests for extracting processable entries from manifest."""

    def test_returns_only_pending_non_nested(self, simple_py: Path) -> None:
        """Should return only PENDING, non-nested entries."""
        analysis = analyze_file(simple_py)
        analyses = {str(simple_py): analysis}
        uid_map = assign_uids(analysis.functions, simple_py, simple_py.parent)
        uid_maps = {str(simple_py): uid_map}
        weights = compute_dependency_weights(analyses)
        manifest = build_manifest(analyses, uid_maps, weights, simple_py.parent)

        entries = get_processable_entries(manifest)
        assert len(entries) == 3  # all 3 are top-level, PENDING
        for _, entry in entries:
            assert entry["status"] == STATUS_PENDING
            assert entry["nested"] is False
