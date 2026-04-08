"""UID anchor injection — insert START/END comment markers around functions.

Anchors are injected bottom-up (reverse line order) to prevent offset drift
during the injection process itself. After injection, py_compile is used
to verify the source file is still syntactically valid.

Anchor format:
    # UID:<12-char-hex>:START
    def my_function(...):
        ...
    # UID:<12-char-hex>:END
"""

from __future__ import annotations

import py_compile
import tempfile
from pathlib import Path

from daio.cartographer.ast_walker import FunctionInfo


# Anchor comment templates
_ANCHOR_START = "# UID:{uid}:START"
_ANCHOR_END = "# UID:{uid}:END"


def build_anchor_start(uid: str) -> str:
    """Build the START anchor comment for a given UID.

    Args:
        uid: 12-character hex UID string.

    Returns:
        Formatted anchor start comment.
    """
    return _ANCHOR_START.format(uid=uid)


def build_anchor_end(uid: str) -> str:
    """Build the END anchor comment for a given UID.

    Args:
        uid: 12-character hex UID string.

    Returns:
        Formatted anchor end comment.
    """
    return _ANCHOR_END.format(uid=uid)


def _determine_indentation(line: str) -> str:
    """Extract the leading whitespace from a line.

    Args:
        line: Source code line.

    Returns:
        The leading whitespace string (spaces/tabs).
    """
    return line[: len(line) - len(line.lstrip())]


def inject_anchors(
    filepath: Path,
    functions: list[FunctionInfo],
    uid_map: dict[str, str],
    *,
    dry_run: bool = False,
) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Inject UID anchor comments around functions in a source file.

    Functions are processed in reverse line order (bottom-up) to avoid
    offset drift during injection. After injection, the modified source
    is validated with py_compile.

    Args:
        filepath: Path to the source file to modify.
        functions: List of FunctionInfo objects (from AST walker).
        uid_map: Dict mapping function name to UID string.
        dry_run: If True, return modified lines but do not write to disk.

    Returns:
        Tuple of:
            - Modified source lines (list of strings, no trailing newlines).
            - Dict mapping UID to (new_start_line, new_end_line) after injection.

    Raises:
        SyntaxError: If the modified source fails py_compile validation.
        KeyError: If a function name is not found in uid_map.
    """
    lines = filepath.read_text(encoding="utf-8").splitlines()

    # Filter to non-nested functions that have UIDs
    processable = [
        f for f in functions
        if not f.nested and f.name in uid_map
    ]

    # Sort by start_line DESCENDING (bottom-up) to avoid offset drift
    processable.sort(key=lambda f: f.start_line, reverse=True)

    # Track new positions after injection
    new_positions: dict[str, tuple[int, int]] = {}

    for func in processable:
        uid = uid_map[func.name]
        start_anchor = build_anchor_start(uid)
        end_anchor = build_anchor_end(uid)

        # Determine indentation from the function's def line
        # For methods inside classes, the anchor should be at the same
        # indentation level as the def line
        def_line_idx = func.start_line - 1  # 0-indexed
        if 0 <= def_line_idx < len(lines):
            indent = _determine_indentation(lines[def_line_idx])
        else:
            indent = ""

        # Insert END anchor AFTER the last line of the function
        end_idx = func.end_line  # 0-indexed position AFTER end_line
        lines.insert(end_idx, f"{indent}{end_anchor}")

        # Insert START anchor BEFORE the first line of the function
        start_idx = func.start_line - 1  # 0-indexed
        lines.insert(start_idx, f"{indent}{start_anchor}")

        # New positions (1-indexed): start_anchor is at start_idx+1,
        # function starts at start_idx+2, ends at end_idx+1 (shifted by 1
        # from the START insert), end_anchor is at end_idx+2
        new_start = start_idx + 1  # anchor line
        new_end = end_idx + 2      # anchor line (shifted by start insert)
        new_positions[uid] = (new_start, new_end)

    # Validate the modified source
    modified_source = "\n".join(lines) + "\n"
    _validate_syntax(modified_source, filepath)

    if not dry_run:
        # Atomic write: write to temp file, then replace
        tmp_path = filepath.with_suffix(".py.daio_tmp")
        try:
            tmp_path.write_text(modified_source, encoding="utf-8")
            tmp_path.replace(filepath)
        except Exception:
            # Clean up temp file on failure
            tmp_path.unlink(missing_ok=True)
            raise

    return lines, new_positions


def _validate_syntax(source: str, filepath: Path) -> None:
    """Validate that source code is syntactically valid Python.

    Uses py_compile to check syntax without executing the code.

    Args:
        source: Python source code as a string.
        filepath: Original filepath (used for error messages).

    Raises:
        SyntaxError: If the source has syntax errors.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        encoding="utf-8",
        delete=True,
    ) as tmp:
        tmp.write(source)
        tmp.flush()
        try:
            py_compile.compile(tmp.name, doraise=True)
        except py_compile.PyCompileError as exc:
            msg = f"Anchor injection corrupted syntax in {filepath}: {exc}"
            raise SyntaxError(msg) from exc


def strip_anchors(source_lines: list[str]) -> list[str]:
    """Remove all UID anchor comments from source lines.

    Useful for cleanup or rollback scenarios.

    Args:
        source_lines: List of source code lines.

    Returns:
        New list with anchor lines removed.
    """
    return [
        line for line in source_lines
        if not line.strip().startswith("# UID:") or
        (":START" not in line and ":END" not in line)
    ]
