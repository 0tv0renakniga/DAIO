"""Offset recalculation — update downstream manifest entries after code replacement.

After a function is replaced with transformed code of a different length,
all downstream line numbers in the same file become stale. This module
recalculates them.

Since we process functions in reverse line order (bottom-up), offset
recalculation only affects already-processed entries above the current
function. This is safe — their transforms are already applied.
"""

from __future__ import annotations

from typing import Any


def recalculate_offsets(
    manifest: dict[str, Any],
    rel_path: str,
    old_start_line: int,
    old_end_line: int,
    new_end_line: int,
) -> None:
    """Update line numbers for all functions in a file after a code replacement.

    Modifies the manifest dict IN PLACE.

    The delta is computed as: new_end - old_end. All functions in the same
    file with start_line > old_start_line get their start_line and end_line
    adjusted by this delta.

    Args:
        manifest: The manifest dict (modified in place).
        rel_path: Relative path of the file that was modified.
        old_start_line: Original START anchor line (1-indexed).
        old_end_line: Original END anchor line (1-indexed).
        new_end_line: New END anchor line after replacement (1-indexed).

    Note:
        Since we process bottom-up, functions with start_line > old_start_line
        have already been processed. Their line numbers need updating for
        consistency (e.g., audit log accuracy), but their code is already
        committed.
    """
    delta = new_end_line - old_end_line

    if delta == 0:
        return  # No change in length — nothing to recalculate

    file_data = manifest.get("files", {}).get(rel_path)
    if file_data is None:
        return

    for entry in file_data.get("functions", []):
        # Only adjust entries ABOVE the current function
        # (which have higher start_line values since manifest is sorted descending)
        # Wait — we process bottom-up, so entries with LOWER start_line
        # (higher up in the file) haven't been processed yet and need adjustment.
        if entry["start_line"] < old_start_line:
            # This function is ABOVE the replaced one — no adjustment needed
            # since insertions below don't affect lines above.
            # Actually: insertions/deletions BELOW a line don't change that
            # line's number. Insertions ABOVE a line shift it down.
            # Since we replaced lines BELOW this function, no shift needed.
            pass
        elif entry["start_line"] > old_start_line:
            # This function is BELOW the replaced one — shift by delta.
            # But since we process bottom-up, this was already processed.
            # We still update for manifest consistency.
            entry["start_line"] += delta
            entry["end_line"] += delta
