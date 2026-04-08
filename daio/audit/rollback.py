"""Rollback mechanism — revert refactored functions using git history.

Provides two rollback strategies:
    1. Per-function: `git revert <commit_hash>` for a specific function's commit.
    2. Full rollback: revert all DAIO commits in reverse order.

Both strategies require auto_commit to have been enabled during the run.
If git is unavailable or commits are missing, falls back to anchor stripping.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console

from daio.cartographer.anchor import strip_anchors

console = Console()


def rollback_function(
    commit_hash: str,
    *,
    cwd: Path | None = None,
) -> bool:
    """Revert a single function's DAIO commit.

    Args:
        commit_hash: Short or full git commit hash to revert.
        cwd: Working directory for git commands.

    Returns:
        True if revert succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "revert", "--no-edit", commit_hash],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(cwd) if cwd else None,
        )
        if result.returncode == 0:
            console.print(f"  [green]✓ Reverted commit {commit_hash}[/]")
            return True

        console.print(f"  [red]✗ Failed to revert {commit_hash}: {result.stderr.strip()[:200]}[/]")
        return False

    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        console.print(f"  [red]✗ Git error reverting {commit_hash}: {exc}[/]")
        return False


def rollback_all(
    surgeon_results: dict[str, Any],
    *,
    cwd: Path | None = None,
) -> dict[str, bool]:
    """Revert all successfully committed DAIO transforms in reverse order.

    Reverses in order to avoid merge conflicts — last commit reverted first.

    Args:
        surgeon_results: Per-function results dict from the Surgeon phase.
        cwd: Working directory for git commands.

    Returns:
        Dict mapping UID to rollback success (True/False).
    """
    # Collect commits in order, then reverse
    commits: list[tuple[str, str]] = []  # (uid, commit_hash)
    for uid, result in surgeon_results.items():
        commit_hash = result.get("commit_hash")
        if commit_hash and result.get("status") == "SUCCESS":
            commits.append((uid, commit_hash))

    # Reverse order for safe rollback
    commits.reverse()

    rollback_results: dict[str, bool] = {}
    console.print(f"  [dim]Rolling back {len(commits)} commits[/]")

    for uid, commit_hash in commits:
        func_name = surgeon_results[uid].get("function", "?")
        console.print(f"  [cyan]Reverting {func_name} ({commit_hash})[/]")
        success = rollback_function(commit_hash, cwd=cwd)
        rollback_results[uid] = success

    succeeded = sum(1 for v in rollback_results.values() if v)
    failed = sum(1 for v in rollback_results.values() if not v)
    console.print(f"  [dim]Rollback complete: {succeeded} reverted, {failed} failed[/]")

    return rollback_results


def strip_all_anchors(
    manifest: dict[str, Any],
    base_path: Path,
) -> int:
    """Remove all UID anchor comments from files listed in the manifest.

    This is the fallback cleanup mechanism when git rollback is unavailable
    or when the user wants to remove anchors after a successful run.

    Args:
        manifest: The manifest dict.
        base_path: Root of the target codebase.

    Returns:
        Number of files modified.
    """
    modified = 0
    base = base_path.resolve()

    for rel_path in manifest.get("files", {}):
        filepath = base / rel_path
        if not filepath.exists():
            continue

        source_lines = filepath.read_text(encoding="utf-8").splitlines()
        cleaned = strip_anchors(source_lines)

        if len(cleaned) != len(source_lines):
            filepath.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
            modified += 1

    console.print(f"  [dim]Stripped anchors from {modified} files[/]")
    return modified
