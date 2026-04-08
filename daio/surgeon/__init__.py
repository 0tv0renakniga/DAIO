"""Surgeon phase — LLM dispatch, validation, code application, offset recalc.

The core refinement loop. For each work packet:
    1. Dispatch to Ollama (🟡 PROBABILISTIC)
    2. Extract transformed code via regex (🔵 DETERMINISTIC)
    3. Validate with py_compile + ruff + LOC sanity (🔵 DETERMINISTIC GATEKEEPER)
    4. Apply delete-and-reinsert (🔵 DETERMINISTIC)
    5. Recalculate downstream offsets (🔵 DETERMINISTIC)
    6. Git commit per function (🔵 DETERMINISTIC, configurable)

Usage:
    from daio.surgeon import run
    manifest = run(config, manifest, work_packets)
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from daio.audit.dataset import export_training_pair
from daio.config import DAIOConfig
from daio.surgeon.dispatch import DispatchError, dispatch
from daio.surgeon.applicator import apply_transform
from daio.surgeon.extractor import ExtractionError, extract_transformed_code
from daio.surgeon.offset import recalculate_offsets
from daio.surgeon.validator import validate
from daio.sieve.work_packet import WorkPacket

console = Console()


def _build_retry_prompt(packet: WorkPacket, error_msg: str) -> str:
    """Inject error context into the work packet for a retry attempt.

    Args:
        packet: The original work packet.
        error_msg: Error message from the failed validation.

    Returns:
        Modified prompt text with error context appended.
    """
    return (
        packet.packet_text
        + f"\n\n=== PREVIOUS ATTEMPT FAILED ===\n"
        f"Error: {error_msg}\n"
        f"Fix the issues above and return the corrected function. "
        f"Preserve the UID markers exactly.\n"
    )


def _git_commit(filepath: Path, func_name: str, uid: str) -> str | None:
    """Git add and commit a single file with a descriptive message.

    Args:
        filepath: Absolute path to the modified file.
        func_name: Name of the refactored function.
        uid: UID of the function.

    Returns:
        Commit hash string on success, None on failure.
    """
    try:
        subprocess.run(
            ["git", "add", str(filepath)],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        result = subprocess.run(
            [
                "git", "commit",
                "-m", f"daio: refactored {func_name} [{uid}]",
                "--no-verify",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            # Extract commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return hash_result.stdout.strip() if hash_result.returncode == 0 else "unknown"
        return None
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return None


def run(
    config: DAIOConfig,
    manifest: dict[str, Any],
    work_packets: list[WorkPacket],
) -> dict[str, Any]:
    """Execute the Surgeon phase — the core refinement loop.

    For each work packet:
        1. Dispatch to Ollama
        2. Extract transformed code
        3. Validate (py_compile + ruff + LOC sanity)
        4. On failure: retry with error injection (up to max_retries)
        5. On success: apply via delete-and-reinsert
        6. Recalculate offsets in manifest
        7. Git commit (if auto_commit enabled)

    Args:
        config: Validated DAIOConfig instance.
        manifest: The manifest dict (modified in place).
        work_packets: Work packets from the Sieve phase.

    Returns:
        Results dict mapping UID to per-function outcome:
        {uid: {"status", "retries", "errors", "commit_hash", "duration_seconds"}}
    """
    base_path = Path(manifest.get("base_path", config.target_path)).resolve()
    results: dict[str, Any] = {}
    total = len(work_packets)

    console.print(f"  [dim]Processing {total} functions sequentially[/]")

    for idx, packet in enumerate(work_packets, 1):
        func_label = f"[{idx}/{total}] {packet.function_name} ({packet.file_path})"
        console.print(f"  [cyan]{func_label}[/]")

        start_time = time.monotonic()
        entry_result: dict[str, Any] = {
            "uid": packet.uid,
            "function": packet.function_name,
            "file": packet.file_path,
            "status": "PENDING",
            "retries": 0,
            "errors": [],
            "commit_hash": None,
            "duration_seconds": 0.0,
        }

        prompt = packet.packet_text
        success = False

        for attempt in range(1, config.max_retries + 1):
            # Step 1: Dispatch to selected backend
            console.print(f"    [dim]Attempt {attempt}/{config.max_retries} — dispatching to {config.model}[/]")

            try:
                response_text = dispatch(prompt=prompt, config=config)
            except DispatchError as exc:
                error_msg = str(exc)
                entry_result["errors"].append(f"Attempt {attempt}: {error_msg}")
                console.print(f"    [red]✗ Dispatch error: {error_msg[:100]}[/]")
                entry_result["retries"] = attempt
                continue

            # Step 2: Extract transformed code
            try:
                transformed_lines = extract_transformed_code(response_text, packet.uid)
            except ExtractionError as exc:
                error_msg = str(exc)
                entry_result["errors"].append(f"Attempt {attempt}: {error_msg}")
                console.print(f"    [red]✗ Extraction failed: {error_msg[:100]}[/]")
                prompt = _build_retry_prompt(packet, "You must preserve UID markers. " + error_msg)
                entry_result["retries"] = attempt
                continue

            # Step 3: Validate
            val_result = validate(
                original_lines=packet.snippet_lines,
                transformed_lines=transformed_lines,
                ruff_config=config.ruff_config,
                shrink_floor=config.loc_shrink_floor,
                growth_ceiling=config.loc_growth_ceiling,
                enable_sast=config.enable_sast,
                sast_tool=config.sast_tool.value,
                enable_typecheck=config.enable_typecheck,
                type_checker=config.type_checker.value,
            )

            if val_result.passed:
                # Step 4: Apply
                filepath = base_path / packet.file_path
                try:
                    old_start, old_end, new_end = apply_transform(
                        filepath, packet.uid, transformed_lines
                    )
                except (ValueError, OSError) as exc:
                    error_msg = f"Apply failed: {exc}"
                    entry_result["errors"].append(f"Attempt {attempt}: {error_msg}")
                    console.print(f"    [red]✗ {error_msg[:100]}[/]")
                    entry_result["retries"] = attempt
                    continue

                # Step 5: Recalculate offsets
                recalculate_offsets(manifest, packet.file_path, old_start, old_end, new_end)

                # Step 6: Git commit
                if config.auto_commit:
                    commit_hash = _git_commit(filepath, packet.function_name, packet.uid)
                    entry_result["commit_hash"] = commit_hash
                    if commit_hash:
                        console.print(f"    [dim]Committed: {commit_hash}[/]")

                entry_result["status"] = "SUCCESS"
                entry_result["retries"] = attempt - 1
                success = True

                if config.dataset_export_enabled:
                    export_training_pair(
                        output_path=config.dataset_output_path,
                        work_packet_text=packet.packet_text,
                        transformed_code="\n".join(transformed_lines),
                        metadata={
                            "uid": packet.uid,
                            "function_name": packet.function_name,
                            "file_path": packet.file_path,
                            "backend": config.backend.value,
                            "model": config.model,
                        },
                    )
                console.print(f"    [green]✓ Applied successfully[/]")
                break

            else:
                # Validation failed — inject errors and retry
                error_summary = "; ".join(val_result.errors)
                entry_result["errors"].append(f"Attempt {attempt}: {error_summary}")
                console.print(
                    f"    [yellow]✗ Validation failed: "
                    f"syntax={'✓' if val_result.syntax_ok else '✗'} "
                    f"lint={'✓' if val_result.lint_ok else '✗'} "
                    f"loc={'✓' if val_result.loc_ok else '✗'}[/]"
                )
                prompt = _build_retry_prompt(packet, error_summary)
                entry_result["retries"] = attempt

        if not success:
            entry_result["status"] = "FAILED"
            console.print(f"    [red]✗ FAILED after {config.max_retries} attempts[/]")

            # Update manifest entry
            _update_manifest_status(manifest, packet.file_path, packet.uid, "FAILED")

        entry_result["duration_seconds"] = round(time.monotonic() - start_time, 2)
        results[packet.uid] = entry_result

    # Summary
    succeeded = sum(1 for r in results.values() if r["status"] == "SUCCESS")
    failed = sum(1 for r in results.values() if r["status"] == "FAILED")
    console.print(
        f"  [dim]Surgeon complete: {succeeded} succeeded, {failed} failed[/]"
    )

    return results


def _update_manifest_status(
    manifest: dict[str, Any],
    rel_path: str,
    uid: str,
    status: str,
) -> None:
    """Update a function's status in the manifest.

    Args:
        manifest: The manifest dict (modified in place).
        rel_path: Relative path to the file.
        uid: UID of the function.
        status: New status string.
    """
    file_data = manifest.get("files", {}).get(rel_path)
    if file_data is None:
        return
    for entry in file_data.get("functions", []):
        if entry.get("uid") == uid:
            entry["status"] = status
            break
