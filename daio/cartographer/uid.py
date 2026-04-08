"""UID generation and collision detection for DAIO function anchors.

UIDs are deterministic: SHA256(filepath_relative + ":" + start_line), truncated
to 12 hex characters. This ensures:
    - Same file + same line always produces the same UID (reproducible)
    - Different files or lines virtually never collide (2^48 space)
    - UIDs are short enough to use as inline comment markers
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from daio.cartographer.ast_walker import FunctionInfo


def generate_uid(filepath: Path, start_line: int, *, base_path: Path | None = None) -> str:
    """Generate a deterministic UID for a function at a specific location.

    Uses SHA256 of the relative filepath + line number, truncated to 12 hex chars.

    Args:
        filepath: Absolute path to the source file.
        start_line: 1-indexed start line of the function.
        base_path: If provided, filepath is made relative to this. Otherwise uses
                   the full absolute path.

    Returns:
        12-character lowercase hex string.

    Examples:
        >>> generate_uid(Path("/project/src/utils.py"), 42, base_path=Path("/project"))
        'a1b2c3d4e5f6'  # (example, actual hash will differ)
    """
    if base_path is not None:
        try:
            rel = filepath.resolve().relative_to(base_path.resolve())
        except ValueError:
            # filepath is not under base_path — use absolute
            rel = filepath.resolve()
    else:
        rel = filepath.resolve()

    key = f"{rel}:{start_line}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return digest[:12]


def assign_uids(
    functions: list[FunctionInfo],
    filepath: Path,
    base_path: Path | None = None,
) -> dict[str, str]:
    """Assign UIDs to a list of functions and verify no collisions.

    Args:
        functions: List of FunctionInfo objects from the AST walker.
        filepath: Absolute path to the source file.
        base_path: Root of the target codebase for relative path computation.

    Returns:
        Dict mapping function name to its UID string.

    Raises:
        ValueError: If any two functions produce the same UID (collision).

    Examples:
        >>> uid_map = assign_uids(functions, Path("src/utils.py"), Path("/project"))
        >>> uid_map
        {'func_a': 'a1b2c3d4e5f6', 'func_b': 'f6e5d4c3b2a1'}
    """
    uid_map: dict[str, str] = {}
    seen_uids: dict[str, str] = {}  # uid → function_name for collision detection

    for func in functions:
        uid = generate_uid(filepath, func.start_line, base_path=base_path)

        if uid in seen_uids:
            existing = seen_uids[uid]
            msg = (
                f"UID collision detected: '{uid}' assigned to both "
                f"'{existing}' and '{func.name}' in {filepath}"
            )
            raise ValueError(msg)

        seen_uids[uid] = func.name
        uid_map[func.name] = uid

    return uid_map


def validate_uid_uniqueness(all_uids: dict[str, dict[str, str]]) -> None:
    """Validate that all UIDs across all files are globally unique.

    Args:
        all_uids: Dict mapping filepath (str) to {func_name: uid} dicts.

    Raises:
        ValueError: If any UID appears in more than one file/function.
    """
    global_seen: dict[str, tuple[str, str]] = {}  # uid → (filepath, func_name)

    for filepath_str, uid_map in all_uids.items():
        for func_name, uid in uid_map.items():
            if uid in global_seen:
                existing_file, existing_func = global_seen[uid]
                msg = (
                    f"Global UID collision: '{uid}' in "
                    f"'{existing_file}::{existing_func}' and "
                    f"'{filepath_str}::{func_name}'"
                )
                raise ValueError(msg)
            global_seen[uid] = (filepath_str, func_name)
