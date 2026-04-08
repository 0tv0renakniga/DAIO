"""Snippet extractor — extract function source code between UID anchors.

Reads source lines and extracts the code block delimited by UID:START/END
markers. Also provides raw line-range extraction for pre-anchor files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# Compiled regex for UID anchor detection
_UID_START_RE = re.compile(r"^(\s*)#\s*UID:([a-f0-9]{12}):START\s*$")
_UID_END_RE = re.compile(r"^(\s*)#\s*UID:([a-f0-9]{12}):END\s*$")


def extract_by_uid(source_lines: list[str], uid: str) -> tuple[list[str], int, int]:
    """Extract source lines between UID anchor markers.

    Extracts the lines BETWEEN the START and END anchors (inclusive of
    the function code, exclusive of the anchor comments themselves).

    Args:
        source_lines: Full source file as a list of lines.
        uid: 12-character hex UID to search for.

    Returns:
        Tuple of:
            - Extracted lines (list of strings, no trailing newlines).
            - Start index (0-indexed) of the first code line after START anchor.
            - End index (0-indexed) of the last code line before END anchor.

    Raises:
        ValueError: If the UID START or END anchor is not found.
    """
    start_idx: int | None = None
    end_idx: int | None = None

    for i, line in enumerate(source_lines):
        start_match = _UID_START_RE.match(line)
        if start_match and start_match.group(2) == uid:
            start_idx = i + 1  # line after START anchor
            continue

        end_match = _UID_END_RE.match(line)
        if end_match and end_match.group(2) == uid:
            end_idx = i - 1  # line before END anchor
            break

    if start_idx is None:
        msg = f"UID START anchor not found for '{uid}'"
        raise ValueError(msg)
    if end_idx is None:
        msg = f"UID END anchor not found for '{uid}'"
        raise ValueError(msg)
    if end_idx < start_idx:
        msg = f"UID anchors for '{uid}' contain no code (empty block)"
        raise ValueError(msg)

    return source_lines[start_idx : end_idx + 1], start_idx, end_idx


def extract_by_line_range(
    source_lines: list[str],
    start_line: int,
    end_line: int,
) -> list[str]:
    """Extract source lines by 1-indexed line range (inclusive).

    Used for pre-anchor extraction when building initial work packets.

    Args:
        source_lines: Full source file as a list of lines.
        start_line: 1-indexed first line (inclusive).
        end_line: 1-indexed last line (inclusive).

    Returns:
        Extracted lines as a list of strings.

    Raises:
        ValueError: If line range is out of bounds.
    """
    if start_line < 1:
        msg = f"start_line must be >= 1, got {start_line}"
        raise ValueError(msg)
    if end_line > len(source_lines):
        msg = f"end_line {end_line} exceeds file length {len(source_lines)}"
        raise ValueError(msg)
    if start_line > end_line:
        msg = f"start_line {start_line} > end_line {end_line}"
        raise ValueError(msg)

    # Convert to 0-indexed
    return source_lines[start_line - 1 : end_line]


def find_all_uids(source_lines: list[str]) -> list[str]:
    """Find all UID values present in source as START anchors.

    Args:
        source_lines: Full source file as a list of lines.

    Returns:
        List of UID strings found (in order of appearance).
    """
    uids: list[str] = []
    for line in source_lines:
        match = _UID_START_RE.match(line)
        if match:
            uids.append(match.group(2))
    return uids
