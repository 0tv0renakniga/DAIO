"""DAIO CLI — Command-line interface for the DAIO pipeline.

Entry points:
    daio run        — Execute the full pipeline
    daio init       — Generate default config.yaml + rules.md template
    daio manifest   — Run Cartographer only, inspect manifest
    daio dry-run    — Generate work packets without dispatching to LLM
    daio validate   — Validate a config file
    daio rollback   — Revert all DAIO commits from a results.json
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from daio import __version__
from daio.config import DAIOConfig, load_config

console = Console()

# ---------------------------------------------------------------------------
# Default template content
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = """\
# DAIO Configuration
# See: https://github.com/your-org/daio for full documentation

# --- LLM ---
model: "qwen2.5-coder:7b-instruct-q8_0"  # Any Ollama-compatible model name
ollama_url: "http://localhost:11434"

# --- Target ---
target_path: "./target"       # Path to the codebase to refactor
rules_path: "./rules.md"      # Refactoring instructions for the LLM
scope: "full"                 # "full" | "module" | "filelist"
# file_list:                  # Uncomment and list files when scope is "filelist"
#   - "src/module_a.py"
#   - "src/module_b.py"

# --- Token Budget ---
token_budget: 4096            # Max tokens per work packet (chars / 4)
header_token_budget: 512      # Max tokens for global header (imports/constants)

# --- Retry & Validation ---
max_retries: 3                # LLM retry attempts on validation failure
loc_shrink_floor: 0.3         # Min output/input LOC ratio (below = hallucination)
loc_growth_ceiling: 3.0       # Max output/input LOC ratio (above = hallucination)

# --- Git ---
auto_commit: true             # Git-commit per successfully refactored function

# --- Infrastructure ---
# ruff_config: "./ruff.toml"  # Optional: path to ruff config
request_timeout: 600          # Ollama HTTP timeout in seconds (CPU can be slow)
output_dir: ".daio"           # Pipeline artifact directory
"""

DEFAULT_RULES = """\
# DAIO Refactoring Rules

## Objective
Add Google-style docstrings to every function and method.

## Requirements
1. Every function MUST have a docstring immediately after the `def` line.
2. Use Google-style format with the following sections (when applicable):
   - One-line summary (imperative mood, e.g., "Compute the factorial.")
   - Extended description (only if the function is non-trivial)
   - Args: parameter name, type, and description
   - Returns: type and description
   - Raises: exception type and condition

## Constraints
- Do NOT modify the function logic, signature, or return values.
- Do NOT add, remove, or reorder imports.
- Do NOT rename variables or parameters.
- Preserve ALL existing comments.
- Preserve the exact indentation style of the original code.

## Example

```python
def calculate_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    \\\"\\\"\\\"Calculate the Euclidean distance between two 2D points.

    Args:
        x1: X-coordinate of the first point.
        y1: Y-coordinate of the first point.
        x2: X-coordinate of the second point.
        y2: Y-coordinate of the second point.

    Returns:
        The Euclidean distance as a float.
    \\\"\\\"\\\"
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
```
"""


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="daio")
def main() -> None:
    """DAIO — Deterministic AI Orchestration.

    A compiler-verified agentic refactoring pipeline.
    """


# ---------------------------------------------------------------------------
# daio init
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--dir",
    "init_dir",
    type=click.Path(),
    default=".",
    help="Directory to initialize (default: current directory).",
)
def init(init_dir: str) -> None:
    """Generate default config.yaml and rules.md templates."""
    target = Path(init_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)

    config_path = target / "config.yaml"
    rules_path = target / "rules.md"

    created: list[str] = []

    if config_path.exists():
        console.print(f"[yellow]⚠ config.yaml already exists at {config_path}, skipping[/]")
    else:
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
        created.append(str(config_path))

    if rules_path.exists():
        console.print(f"[yellow]⚠ rules.md already exists at {rules_path}, skipping[/]")
    else:
        rules_path.write_text(DEFAULT_RULES, encoding="utf-8")
        created.append(str(rules_path))

    if created:
        console.print(
            Panel(
                "\n".join(f"  ✓ {p}" for p in created),
                title="[green]DAIO initialized[/]",
                border_style="green",
            )
        )
    else:
        console.print("[dim]Nothing to create — all files already exist.[/]")


# ---------------------------------------------------------------------------
# daio validate
# ---------------------------------------------------------------------------


@main.command("validate")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to config.yaml to validate.",
)
def validate_config(config_path: str) -> None:
    """Validate a config.yaml file against the DAIO schema."""
    try:
        config = load_config(Path(config_path))
    except Exception as exc:
        console.print(f"[red]✗ Validation failed:[/] {exc}")
        raise SystemExit(1) from exc

    _display_config_table(config)
    console.print("\n[green]✓ Config is valid.[/]")


# ---------------------------------------------------------------------------
# daio manifest
# ---------------------------------------------------------------------------


@main.command("manifest")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to config.yaml.",
)
def show_manifest(config_path: str) -> None:
    """Run the Cartographer phase only and display the manifest."""
    from daio.pipeline import run_manifest_only

    config = load_config(Path(config_path))

    console.print(
        Panel(
            f"Target: {config.target_path}\nScope: {config.scope.value}",
            title="[bold cyan]DAIO Manifest[/]",
            border_style="cyan",
        )
    )

    manifest = run_manifest_only(config)

    # Display summary table
    table = Table(title="Manifest Summary", show_lines=True)
    table.add_column("File", style="cyan")
    table.add_column("Functions", style="white", justify="right")
    table.add_column("Processable", style="green", justify="right")
    table.add_column("Nested", style="yellow", justify="right")

    for rel_path, file_data in manifest.get("files", {}).items():
        funcs = file_data.get("functions", [])
        total = len(funcs)
        processable = sum(1 for f in funcs if not f.get("nested") and f.get("status") == "PENDING")
        nested = sum(1 for f in funcs if f.get("nested"))
        table.add_row(rel_path, str(total), str(processable), str(nested))

    console.print(table)

    total_funcs = sum(
        len(fd.get("functions", []))
        for fd in manifest.get("files", {}).values()
    )
    console.print(f"\n[dim]Total: {total_funcs} functions across {len(manifest.get('files', {}))} files[/]")


# ---------------------------------------------------------------------------
# daio dry-run
# ---------------------------------------------------------------------------


@main.command("dry-run")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to config.yaml.",
)
def dry_run(config_path: str) -> None:
    """Generate work packets and save them without dispatching to the LLM."""
    from daio.pipeline import run_pipeline

    config = load_config(Path(config_path))

    console.print(
        Panel(
            f"Model: {config.model}\n"
            f"Target: {config.target_path}\n"
            f"Scope: {config.scope.value}\n"
            f"Token budget: {config.token_budget}",
            title="[bold yellow]DAIO Dry-Run[/]",
            border_style="yellow",
        )
    )

    exit_code = run_pipeline(config, dry_run=True)
    raise SystemExit(exit_code)


# ---------------------------------------------------------------------------
# daio run
# ---------------------------------------------------------------------------


@main.command("run")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to config.yaml.",
)
def run(config_path: str) -> None:
    """Execute the full DAIO pipeline."""
    from daio.pipeline import run_pipeline

    config = load_config(Path(config_path))

    console.print(
        Panel(
            f"Model: {config.model}\n"
            f"Target: {config.target_path}\n"
            f"Scope: {config.scope.value}\n"
            f"Auto-commit: {config.auto_commit}\n"
            f"Token budget: {config.token_budget}\n"
            f"Max retries: {config.max_retries}",
            title="[bold cyan]DAIO Pipeline[/]",
            border_style="cyan",
        )
    )

    exit_code = run_pipeline(config)
    raise SystemExit(exit_code)


# ---------------------------------------------------------------------------
# daio rollback
# ---------------------------------------------------------------------------


@main.command("rollback")
@click.option(
    "--results",
    "results_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to results.json from a previous run.",
)
@click.option(
    "--cwd",
    "working_dir",
    type=click.Path(exists=True),
    default=".",
    help="Working directory for git commands.",
)
def rollback(results_path: str, working_dir: str) -> None:
    """Revert all DAIO commits from a previous pipeline run."""
    from daio.audit.rollback import rollback_all

    results_file = Path(results_path)
    surgeon_results = json.loads(results_file.read_text(encoding="utf-8"))

    committed = sum(
        1 for r in surgeon_results.values()
        if r.get("commit_hash") and r.get("status") == "SUCCESS"
    )

    if committed == 0:
        console.print("[yellow]No committed transforms found in results — nothing to rollback.[/]")
        return

    console.print(
        Panel(
            f"Results file: {results_file}\n"
            f"Commits to revert: {committed}",
            title="[bold red]DAIO Rollback[/]",
            border_style="red",
        )
    )

    if not click.confirm("Proceed with rollback?"):
        console.print("[dim]Rollback cancelled.[/]")
        return

    results = rollback_all(surgeon_results, cwd=Path(working_dir))
    succeeded = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)

    if failed > 0:
        console.print(f"[yellow]⚠ Rollback partially complete: {succeeded} reverted, {failed} failed[/]")
    else:
        console.print(f"[green]✓ Rollback complete: {succeeded} commits reverted[/]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _display_config_table(config: DAIOConfig) -> None:
    """Render a rich table of all config fields.

    Args:
        config: Validated DAIOConfig instance to display.
    """
    table = Table(title="DAIO Configuration", show_lines=True)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    for field_name, field_info in DAIOConfig.model_fields.items():
        value = getattr(config, field_name)
        desc = field_info.description or ""
        display_val = str(value)
        if value is None:
            display_val = "[dim]<not set>[/dim]"
        table.add_row(f"{field_name}", f"{display_val}\n[dim]{desc}[/dim]")

    console.print(table)
