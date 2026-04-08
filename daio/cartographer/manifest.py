"""Manifest serialization and management for the DAIO pipeline.

The manifest is the central data structure produced by the Cartographer phase.
It records every function discovered in the target codebase along with its
UID, line range, dependency weight, and processing status.

Manifest schema version: 1
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daio.cartographer.ast_walker import FileAnalysis, FunctionInfo


# Status values for manifest entries
STATUS_PENDING = "PENDING"
STATUS_SKIPPED = "SKIPPED"
STATUS_SKIPPED_NESTED = "SKIPPED_NESTED"
STATUS_SKIPPED_OVERSIZED = "SKIPPED_OVERSIZED"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_REVERTED = "REVERTED"

MANIFEST_VERSION = 1


def compute_dependency_weights(
    analyses: dict[str, FileAnalysis],
) -> dict[str, int]:
    """Count how many times each function name is referenced across all files.

    Uses AST Name node visitor to count references. This gives a rough
    measure of how "important" a function is — high-reference functions
    affect more call sites if broken.

    Args:
        analyses: Dict mapping filepath strings to FileAnalysis objects.

    Returns:
        Dict mapping function name to its reference count across all files.
        Functions with zero external references will have weight 0.
    """
    # Collect all function names across all files
    all_func_names: set[str] = set()
    for analysis in analyses.values():
        for func in analysis.functions:
            all_func_names.add(func.name)

    # Count references to those names across all files
    ref_counts: dict[str, int] = {name: 0 for name in all_func_names}

    for analysis in analyses.values():
        source_text = analysis.filepath.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source_text, filename=str(analysis.filepath))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in all_func_names:
                # Don't count the function definition itself
                if not isinstance(
                    getattr(node, "_daio_parent", None),
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    ref_counts[node.id] = ref_counts.get(node.id, 0) + 1

    # Subtract 1 for each function (the def statement itself contains the name)
    # Actually, ast.Name only captures usages in expressions, not def names.
    # FunctionDef.name is a string, not an ast.Name node. So no adjustment needed.

    return ref_counts


def _function_to_entry(
    func: FunctionInfo,
    uid: str,
    dependency_weight: int,
) -> dict[str, Any]:
    """Convert a FunctionInfo + metadata into a manifest entry dict.

    Args:
        func: Function metadata from AST walker.
        uid: Assigned UID string.
        dependency_weight: Reference count for this function.

    Returns:
        Dict suitable for JSON serialization.
    """
    status = STATUS_SKIPPED_NESTED if func.nested else STATUS_PENDING
    return {
        "name": func.name,
        "uid": uid,
        "start_line": func.start_line,
        "end_line": func.end_line,
        "body_loc": func.body_loc,
        "dependency_weight": dependency_weight,
        "has_docstring": func.has_docstring,
        "is_async": func.is_async,
        "is_method": func.is_method,
        "class_name": func.class_name,
        "decorators": func.decorators,
        "nested": func.nested,
        "status": status,
        "dirty": True,
    }


def build_manifest(
    analyses: dict[str, FileAnalysis],
    uid_maps: dict[str, dict[str, str]],
    dependency_weights: dict[str, int],
    base_path: Path,
) -> dict[str, Any]:
    """Build the full manifest dict from analyzed files.

    Functions within each file are sorted by start_line DESCENDING
    (reverse line order) for bottom-up processing.

    Args:
        analyses: Dict mapping filepath strings to FileAnalysis objects.
        uid_maps: Dict mapping filepath strings to {func_name: uid} dicts.
        dependency_weights: Global reference counts by function name.
        base_path: Root of the target codebase.

    Returns:
        Complete manifest dict ready for JSON serialization.
    """
    files: dict[str, Any] = {}

    for filepath_str, analysis in sorted(analyses.items()):
        uids = uid_maps.get(filepath_str, {})
        rel_path = str(analysis.filepath.relative_to(base_path.resolve()))

        entries = []
        for func in analysis.functions:
            uid = uids.get(func.name, "")
            weight = dependency_weights.get(func.name, 0)
            entries.append(_function_to_entry(func, uid, weight))

        # Sort by start_line DESCENDING for bottom-up processing
        entries.sort(key=lambda e: e["start_line"], reverse=True)

        files[rel_path] = {
            "functions": entries,
            "parse_error": analysis.parse_error,
        }

    return {
        "version": MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_path": str(base_path.resolve()),
        "files": files,
    }


def save_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    """Write the manifest to a JSON file.

    Args:
        manifest: Complete manifest dict.
        output_path: Path to write the manifest.json file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load a manifest from a JSON file.

    Args:
        manifest_path: Path to the manifest.json file.

    Returns:
        Parsed manifest dict.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    if not manifest_path.exists():
        msg = f"Manifest not found: {manifest_path}"
        raise FileNotFoundError(msg)

    return json.loads(manifest_path.read_text(encoding="utf-8"))


def get_processable_entries(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Extract all processable (non-nested, PENDING) function entries.

    Returns entries in their stored order (reverse line order within files).

    Args:
        manifest: Loaded manifest dict.

    Returns:
        List of (relative_filepath, entry_dict) tuples.
    """
    results: list[tuple[str, dict[str, Any]]] = []
    for rel_path, file_data in manifest.get("files", {}).items():
        for entry in file_data.get("functions", []):
            if entry.get("status") == STATUS_PENDING and not entry.get("nested", False):
                results.append((rel_path, entry))
    return results
