"""Cartographer phase — AST parsing, UID generation, anchor injection, manifest building.

Usage:
    from daio.cartographer import run
    manifest = run(config)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from daio.cartographer.anchor import inject_anchors
from daio.cartographer.ast_walker import FileAnalysis, analyze_file, collect_files
from daio.cartographer.manifest import (
    build_manifest,
    compute_dependency_weights,
    save_manifest,
)
from daio.cartographer.uid import assign_uids, validate_uid_uniqueness
from daio.config import DAIOConfig

console = Console()


def run(config: DAIOConfig, *, inject: bool = True) -> dict[str, Any]:
    """Execute the Cartographer phase.

    Steps:
        1. Collect Python files from target path based on scope.
        2. AST-walk each file to extract function definitions.
        3. Generate UIDs for each function and validate uniqueness.
        4. Compute dependency weights across all files.
        5. Inject UID anchor comments into source files (bottom-up).
        6. Build and save the manifest.

    Args:
        config: Validated DAIOConfig instance.
        inject: Whether to inject UID anchors into source files.
            Set to False for dry-run / manifest-only mode.

    Returns:
        The complete manifest dict.

    Raises:
        ValueError: If UID collisions are detected.
        SyntaxError: If anchor injection corrupts source syntax.
    """
    base_path = config.target_path.resolve()
    output_dir = config.output_dir
    scope = config.scope.value
    file_list = config.file_list

    # Step 1: Collect files
    console.print(f"  [dim]Scanning {base_path} (scope: {scope})[/]")
    py_files = collect_files(base_path, scope, file_list)
    console.print(f"  [dim]Found {len(py_files)} Python files[/]")

    if not py_files:
        console.print("  [yellow]⚠ No Python files found in target path[/]")
        return build_manifest({}, {}, {}, base_path)

    # Step 2: AST-walk each file
    analyses: dict[str, FileAnalysis] = {}
    total_functions = 0
    parse_errors = 0

    for py_file in py_files:
        analysis = analyze_file(py_file)
        key = str(py_file)
        analyses[key] = analysis

        if analysis.parse_error:
            parse_errors += 1
            console.print(f"  [red]✗ Parse error in {py_file.name}: {analysis.parse_error}[/]")
        else:
            total_functions += len(analysis.functions)

    console.print(f"  [dim]Extracted {total_functions} functions from {len(analyses)} files[/]")
    if parse_errors:
        console.print(f"  [yellow]⚠ {parse_errors} files had parse errors (skipped)[/]")

    # Step 3: Generate UIDs
    uid_maps: dict[str, dict[str, str]] = {}
    for filepath_str, analysis in analyses.items():
        if analysis.parse_error:
            continue
        uid_map = assign_uids(analysis.functions, analysis.filepath, base_path)
        uid_maps[filepath_str] = uid_map

    # Validate global uniqueness
    validate_uid_uniqueness(uid_maps)
    console.print(f"  [dim]Generated {sum(len(m) for m in uid_maps.values())} unique UIDs[/]")

    # Step 4: Compute dependency weights
    dep_weights = compute_dependency_weights(analyses)

    # Step 5: Inject anchors (if not dry-run)
    if inject:
        injected_count = 0
        for filepath_str, analysis in analyses.items():
            if analysis.parse_error:
                continue
            uid_map = uid_maps.get(filepath_str, {})
            processable = [f for f in analysis.functions if not f.nested and f.name in uid_map]
            if processable:
                inject_anchors(analysis.filepath, analysis.functions, uid_map)
                injected_count += len(processable)
        console.print(f"  [dim]Injected anchors for {injected_count} functions[/]")
    else:
        console.print("  [dim]Skipping anchor injection (dry-run mode)[/]")

    # Step 6: Build and save manifest
    manifest = build_manifest(analyses, uid_maps, dep_weights, base_path)
    manifest_path = output_dir / "manifest.json"
    save_manifest(manifest, manifest_path)
    console.print(f"  [green]✓ Manifest saved to {manifest_path}[/]")

    return manifest
