"""Applicator — delete-and-reinsert transformed code into source files.

Atomically replaces the original function code between UID anchors
with the validated, transformed code. Uses write-to-tmp + os.replace()
for POSIX atomic write safety.
"""

from __future__ import annotations

import os
from pathlib import Path


def apply_transform(
    filepath: Path,
    uid: str,
    transformed_lines: list[str],
) -> tuple[int, int, int]:
    """Replace the code between UID anchors with transformed code.

    Reads the file, locates the START/END anchors, replaces the code
    between them, and writes atomically.

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

    # Preserve the anchor lines themselves and their indentation
    start_anchor_line = lines[start_idx]
    end_anchor_line = lines[end_idx]

    # Get indentation from the existing code to apply to transformed lines
    # (the transformed code should already be properly indented from the LLM)

    # Build new file content:
    # [lines before START anchor]
    # [START anchor]
    # [transformed code]
    # [END anchor]
    # [lines after END anchor]
    new_lines = (
        lines[: start_idx]           # before START
        + [start_anchor_line]         # START anchor
        + transformed_lines           # new code
        + [end_anchor_line]           # END anchor
        + lines[end_idx + 1 :]        # after END
    )

    # Atomic write
    new_content = "\n".join(new_lines) + "\n"
    tmp_path = filepath.with_suffix(".py.daio_tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(str(tmp_path), str(filepath))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    # Return 1-indexed line numbers
    old_start_line = start_idx + 1
    old_end_line = end_idx + 1
    new_end_line = start_idx + 1 + len(transformed_lines) + 1  # start + code + end anchor

    return old_start_line, old_end_line, new_end_line
