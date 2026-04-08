"""Summary report generator — human-readable post-run report.

Produces a Markdown summary of the pipeline run, including:
    - Configuration overview
    - Per-file results table
    - Failed function details with error messages
    - Timing breakdown
    - Final status counts
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def generate_report(
    manifest: dict[str, Any],
    surgeon_results: dict[str, Any],
    config_summary: dict[str, Any],
    duration_seconds: float,
) -> str:
    """Generate a Markdown summary report of the pipeline run.

    Args:
        manifest: The final manifest dict (after Surgeon).
        surgeon_results: Per-function results from the Surgeon phase.
        config_summary: Key config values for the report header.
        duration_seconds: Total pipeline wall-clock time.

    Returns:
        Complete Markdown report as a string.
    """
    lines: list[str] = []

    # Header
    lines.append("# DAIO Pipeline Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Duration:** {duration_seconds:.1f}s")
    lines.append(f"**Model:** {config_summary.get('model', 'unknown')}")
    lines.append(f"**Target:** {config_summary.get('target_path', 'unknown')}")
    lines.append("")

    # Status counts
    succeeded = sum(1 for r in surgeon_results.values() if r.get("status") == "SUCCESS")
    failed = sum(1 for r in surgeon_results.values() if r.get("status") == "FAILED")
    total = len(surgeon_results)

    # Count skipped from manifest
    skipped = 0
    for file_data in manifest.get("files", {}).values():
        for entry in file_data.get("functions", []):
            status = entry.get("status", "")
            if status.startswith("SKIPPED"):
                skipped += 1

    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| ✅ Succeeded | {succeeded} |")
    lines.append(f"| ❌ Failed | {failed} |")
    lines.append(f"| ⏭️ Skipped | {skipped} |")
    lines.append(f"| **Total processed** | **{total}** |")
    lines.append("")

    # Per-function results table
    if surgeon_results:
        lines.append("## Function Results")
        lines.append("")
        lines.append("| Function | File | Status | Retries | Time (s) | Commit |")
        lines.append("|----------|------|--------|---------|----------|--------|")

        for uid, result in surgeon_results.items():
            func = result.get("function", "?")
            fpath = result.get("file", "?")
            status = result.get("status", "?")
            retries = result.get("retries", 0)
            dur = result.get("duration_seconds", 0.0)
            commit = result.get("commit_hash") or "—"

            status_icon = "✅" if status == "SUCCESS" else "❌"
            lines.append(f"| {func} | {fpath} | {status_icon} {status} | {retries} | {dur:.1f} | {commit} |")

        lines.append("")

    # Failed function details
    failures = {uid: r for uid, r in surgeon_results.items() if r.get("status") == "FAILED"}
    if failures:
        lines.append("## Failed Functions")
        lines.append("")
        for uid, result in failures.items():
            func = result.get("function", "?")
            fpath = result.get("file", "?")
            lines.append(f"### {func} ({fpath})")
            lines.append(f"**UID:** `{uid}`")
            lines.append("")
            errors = result.get("errors", [])
            if errors:
                lines.append("**Errors:**")
                for err in errors:
                    lines.append(f"- {err}")
            lines.append("")

    # Timing
    lines.append("## Timing")
    lines.append("")
    if surgeon_results:
        times = [r.get("duration_seconds", 0.0) for r in surgeon_results.values()]
        avg_time = sum(times) / len(times) if times else 0
        max_time = max(times) if times else 0
        lines.append(f"- **Total wall-clock:** {duration_seconds:.1f}s")
        lines.append(f"- **Average per function:** {avg_time:.1f}s")
        lines.append(f"- **Slowest function:** {max_time:.1f}s")
    else:
        lines.append(f"- **Total wall-clock:** {duration_seconds:.1f}s")
        lines.append("- No functions processed")

    lines.append("")
    return "\n".join(lines)


def save_report(report_text: str, output_path: Path) -> None:
    """Write the report to a Markdown file.

    Args:
        report_text: The complete report string.
        output_path: Path to write the report.md file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")


def save_results_json(
    surgeon_results: dict[str, Any],
    output_path: Path,
) -> None:
    """Write the raw results as JSON for programmatic consumption.

    Args:
        surgeon_results: Per-function results dict from the Surgeon.
        output_path: Path to write the results.json file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(surgeon_results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
