"""Audit Trail phase — report generation, audit finalization, optional anchor cleanup.

Usage:
    from daio.audit import run
    run(config, manifest, surgeon_results, audit_logger, duration)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from daio.audit.logger import AuditLogger
from daio.audit.report import generate_report, save_report, save_results_json
from daio.audit.rollback import strip_all_anchors
from daio.config import DAIOConfig

console = Console()


def run(
    config: DAIOConfig,
    manifest: dict[str, Any],
    surgeon_results: dict[str, Any],
    audit_logger: AuditLogger,
    duration_seconds: float,
    *,
    strip_anchors_on_complete: bool = False,
) -> None:
    """Execute the Audit Trail phase.

    Steps:
        1. Generate Markdown summary report.
        2. Save raw results as JSON.
        3. Save final manifest snapshot.
        4. Log pipeline completion event.
        5. Optionally strip UID anchors from source files.

    Args:
        config: Validated DAIOConfig instance.
        manifest: The final manifest dict.
        surgeon_results: Per-function results from the Surgeon.
        audit_logger: The AuditLogger instance used throughout the run.
        duration_seconds: Total pipeline wall-clock time.
        strip_anchors_on_complete: If True, remove all UID anchors after report.
    """
    output_dir = config.output_dir
    base_path = Path(manifest.get("base_path", config.target_path)).resolve()

    config_summary = {
        "model": config.model,
        "target_path": str(config.target_path),
        "scope": config.scope.value,
        "token_budget": config.token_budget,
        "max_retries": config.max_retries,
        "auto_commit": config.auto_commit,
    }

    # Step 1: Generate report
    report_text = generate_report(manifest, surgeon_results, config_summary, duration_seconds)
    report_path = output_dir / "report.md"
    save_report(report_text, report_path)
    console.print(f"  [green]✓ Report saved to {report_path}[/]")

    # Step 2: Save raw results
    results_path = output_dir / "results.json"
    save_results_json(surgeon_results, results_path)
    console.print(f"  [green]✓ Results saved to {results_path}[/]")

    # Step 3: Log pipeline end
    succeeded = sum(1 for r in surgeon_results.values() if r.get("status") == "SUCCESS")
    failed = sum(1 for r in surgeon_results.values() if r.get("status") == "FAILED")

    audit_logger.log_pipeline_end({
        "succeeded": succeeded,
        "failed": failed,
        "total": len(surgeon_results),
        "duration_seconds": round(duration_seconds, 2),
    })
    console.print(f"  [green]✓ Audit log finalized at {audit_logger.log_path}[/]")

    # Step 4: Optional anchor cleanup
    if strip_anchors_on_complete:
        console.print("  [dim]Stripping UID anchors from source files...[/]")
        modified = strip_all_anchors(manifest, base_path)
        console.print(f"  [green]✓ Stripped anchors from {modified} files[/]")
