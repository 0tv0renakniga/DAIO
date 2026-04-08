"""Applicator — delete-and-reinsert transformed code into source files.

Atomically replaces the original function code between UID anchors
with the validated, transformed code. Uses write-to-tmp + shutil.move()
for cross-filesystem safety (Docker volumes, NFS mounts).

V1.1 fixes:
    - #2: Auto-indentation alignment (dedent + re-indent LLM output)
    - #6: Cross-filesystem atomic write (shutil.move replaces os.replace)
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path


def _realign_indentation(
    original_lines: list[str],
    transformed_lines: list[str],
) -> list[str]:
    """Align LLM output indentation to match the original function's indent.

    LLMs frequently strip leading whitespace, especially for class methods.
    This normalizes by dedenting the LLM output, then re-indenting to match
    the original def line's whitespace.

    Args:
        original_lines: The original function code between anchors.
        transformed_lines: The LLM-generated replacement code.

    Returns:
        Re-indented transformed lines.
    """
    if not transformed_lines or not original_lines:
        return transformed_lines

    # Detect original indent from the first non-blank line (should be `def ...`)
    original_indent = ""
    for line in original_lines:
        stripped = line.lstrip()
        if stripped.startswith(("def ", "async def ", "@")):
            original_indent = line[: len(line) - len(stripped)]
            break

    # Detect transformed indent from the first def/async def/decorator line
    transformed_indent = ""
    for line in transformed_lines:
        stripped = line.lstrip()
        if stripped.startswith(("def ", "async def ", "@")):
            transformed_indent = line[: len(line) - len(stripped)]
            break

    # If indentation already matches, return as-is
    if original_indent == transformed_indent:
        return transformed_lines

    # Dedent the transformed code, then re-indent to match original
    code_block = "\n".join(transformed_lines)
    dedented = textwrap.dedent(code_block)
    if original_indent:
        reindented = textwrap.indent(dedented, original_indent)
    else:
        reindented = dedented

    return reindented.splitlines()


def apply_transform(
    filepath: Path,
    uid: str,
    transformed_lines: list[str],
) -> tuple[int, int, int]:
    """Replace the code between UID anchors with transformed code.

    Reads the file, locates the START/END anchors, re-aligns indentation,
    replaces the code, and writes atomically via shutil.move().

    Args:
        filepath: Path to the source file.
        uid: UID of the function being replaced.
        transformed_lines: The validated new code lines.

    Returns:
        Tuple of (old_start, old_end, new_end) as 1-indexed line numbers.
        - old_start: Line number of the START anchor.
        - old_end: Line number of the END anchor.
        - new_end: New line number of the END anchor after replacement.

    Raises:
        ValueError: If UID anchors are not found.
        OSError: If atomic write fails.
    """
    lines = filepath.read_text(encoding="utf-8").splitlines()

    start_marker = f"# UID:{uid}:START"
    end_marker = f"# UID:{uid}:END"

    start_idx: int | None = None
    end_idx: int | None = None

    for i, line in enumerate(lines):
        if start_marker in line.strip():
            start_idx = i
        elif end_marker in line.strip():
            end_idx = i
            break

    if start_idx is None:
        msg = f"UID START anchor not found for '{uid}' in {filepath}"
        raise ValueError(msg)
    if end_idx is None:
        msg = f"UID END anchor not found for '{uid}' in {filepath}"
        raise ValueError(msg)

    # Preserve anchor lines
    start_anchor_line = lines[start_idx]
    end_anchor_line = lines[end_idx]

    # Extract original code between anchors for indent detection
    original_code_lines = lines[start_idx + 1 : end_idx]

    # Fix #2: Auto-indentation alignment
    aligned_lines = _realign_indentation(original_code_lines, transformed_lines)

    # Build new file content
    new_lines = (
        lines[: start_idx]           # before START
        + [start_anchor_line]         # START anchor
        + aligned_lines               # re-indented new code
        + [end_anchor_line]           # END anchor
        + lines[end_idx + 1 :]        # after END
    )

    # Fix #6: Cross-filesystem atomic write via shutil.move
    new_content = "\n".join(new_lines) + "\n"
    tmp_path = filepath.with_suffix(".py.daio_tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        shutil.move(str(tmp_path), str(filepath))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    # Return 1-indexed line numbers
    old_start_line = start_idx + 1
    old_end_line = end_idx + 1
    new_end_line = start_idx + 1 + len(aligned_lines) + 1

    return old_start_line, old_end_line, new_end_line
