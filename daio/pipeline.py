"""DAIO pipeline orchestrator — sequential phase executor.

Executes: Cartographer → Sieve → Surgeon → Audit Trail
Each phase has an explicit error boundary. If any phase fails,
the pipeline aborts cleanly with a diagnostic message.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Console

from daio.audit import run as audit_run
from daio.audit.logger import AuditLogger
from daio.cartographer import run as cartographer_run
from daio.cartographer.manifest import save_manifest
from daio.config import DAIOConfig
from daio.sieve import run as sieve_run
from daio.surgeon import run as surgeon_run

console = Console()


def run_pipeline(config: DAIOConfig, *, dry_run: bool = False) -> int:
    """Execute the full DAIO pipeline sequentially.

    Phases:
        1. Cartographer — AST parse, build manifest, inject UIDs
        2. Sieve — Extract snippets, build work packets
        3. Surgeon — Dispatch to LLM, validate, apply, recalc offsets
        4. Audit Trail — Report, audit log, optional cleanup

    Args:
        config: Validated DAIOConfig instance.
        dry_run: If True, skip Surgeon phase (generate packets only).

    Returns:
        Exit code: 0 if all functions succeeded, 1 if any failed.
    """
    pipeline_start = time.monotonic()

    # Initialize audit logger
    audit_logger = AuditLogger(config.output_dir / "audit.jsonl")
    audit_logger.log_pipeline_start({
        "model": config.model,
        "target_path": str(config.target_path),
        "scope": config.scope.value,
        "token_budget": config.token_budget,
        "max_retries": config.max_retries,
        "auto_commit": config.auto_commit,
        "dry_run": dry_run,
    })

    # ---------------------------------------------------------------
    # Phase 1: Cartographer
    # ---------------------------------------------------------------
    console.print("\n[bold cyan]Phase 1: Cartographer[/] — AST parsing & manifest building")
    try:
        manifest = cartographer_run(config, inject=True)
    except Exception as exc:
        console.print(f"[red]✗ Cartographer failed: {exc}[/]")
        audit_logger.log("PHASE_ERROR", "", "", "", {"phase": "cartographer", "error": str(exc)})
        return 1

    # ---------------------------------------------------------------
    # Phase 2: Sieve
    # ---------------------------------------------------------------
    console.print("\n[bold cyan]Phase 2: Sieve[/] — Context pruning & work packet assembly")
    try:
        work_packets = sieve_run(config, manifest, save_packets=dry_run)
    except Exception as exc:
        console.print(f"[red]✗ Sieve failed: {exc}[/]")
        audit_logger.log("PHASE_ERROR", "", "", "", {"phase": "sieve", "error": str(exc)})
        return 1

    if not work_packets:
        console.print("[yellow]⚠ No processable functions found. Nothing to do.[/]")
        duration = time.monotonic() - pipeline_start
        audit_logger.log_pipeline_end({"succeeded": 0, "failed": 0, "total": 0, "duration_seconds": round(duration, 2)})
        return 0

    console.print(f"  [dim]{len(work_packets)} work packets ready[/]")

    # ---------------------------------------------------------------
    # Phase 3: Surgeon (skip in dry-run mode)
    # ---------------------------------------------------------------
    surgeon_results: dict[str, Any] = {}

    if dry_run:
        console.print("\n[bold yellow]Phase 3: Surgeon[/] — [dim]SKIPPED (dry-run mode)[/]")
        console.print(f"  [dim]Work packets saved to {config.output_dir / 'work_packets'}[/]")
    else:
        console.print("\n[bold cyan]Phase 3: Surgeon[/] — LLM dispatch & refinement loop")
        try:
            surgeon_results = surgeon_run(config, manifest, work_packets)
        except Exception as exc:
            console.print(f"[red]✗ Surgeon failed: {exc}[/]")
            audit_logger.log("PHASE_ERROR", "", "", "", {"phase": "surgeon", "error": str(exc)})
            return 1

    # ---------------------------------------------------------------
    # Phase 4: Audit Trail
    # ---------------------------------------------------------------
    duration = time.monotonic() - pipeline_start
    console.print("\n[bold cyan]Phase 4: Audit Trail[/] — Report & logging")
    try:
        audit_run(config, manifest, surgeon_results, audit_logger, duration)
    except Exception as exc:
        console.print(f"[red]✗ Audit Trail failed: {exc}[/]")
        return 1

    # Save final manifest snapshot
    final_manifest_path = config.output_dir / "manifest.json"
    save_manifest(manifest, final_manifest_path)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    duration = time.monotonic() - pipeline_start
    succeeded = sum(1 for r in surgeon_results.values() if r.get("status") == "SUCCESS")
    failed = sum(1 for r in surgeon_results.values() if r.get("status") == "FAILED")

    console.print()
    if failed == 0 and not dry_run:
        console.print(f"[bold green]✓ Pipeline complete — {succeeded} functions refactored in {duration:.1f}s[/]")
    elif dry_run:
        console.print(f"[bold green]✓ Dry-run complete — {len(work_packets)} work packets generated in {duration:.1f}s[/]")
    else:
        console.print(f"[bold yellow]⚠ Pipeline complete — {succeeded} succeeded, {failed} failed in {duration:.1f}s[/]")

    return 0 if failed == 0 else 1


def run_manifest_only(config: DAIOConfig) -> dict[str, Any]:
    """Run only the Cartographer phase (no injection) and return manifest.

    Args:
        config: Validated DAIOConfig instance.

    Returns:
        The manifest dict.
    """
    console.print("\n[bold cyan]Cartographer[/] — AST parsing (manifest-only, no injection)")
    manifest = cartographer_run(config, inject=False)
    return manifest
