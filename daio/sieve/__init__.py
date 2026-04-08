"""Sieve phase — context pruning and work packet assembly.

Usage:
    from daio.sieve import run
    packets = run(config, manifest)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from daio.cartographer.manifest import STATUS_SKIPPED_OVERSIZED, get_processable_entries
from daio.config import DAIOConfig
from daio.sieve.work_packet import WorkPacket, assemble_work_packet, save_work_packet

console = Console()


def run(
    config: DAIOConfig,
    manifest: dict[str, Any],
    *,
    save_packets: bool = False,
) -> list[WorkPacket]:
    """Execute the Sieve phase — build work packets for all processable functions.

    For each PENDING, non-nested function in the manifest:
        1. Read the source file.
        2. Extract the snippet by UID anchors.
        3. Build a pruned global header (filtered imports + constants).
        4. Assemble the work packet with rules + header + snippet + instruction.
        5. Check the token budget and tag the packet.

    Args:
        config: Validated DAIOConfig instance.
        manifest: The manifest dict from the Cartographer phase.
        save_packets: If True, write each work packet to disk (for dry-run).

    Returns:
        List of WorkPacket objects, one per processable function.
        Functions that exceed 2x token budget are marked SKIPPED_OVERSIZED
        in the manifest and excluded from the returned list.
    """
    base_path = Path(manifest.get("base_path", config.target_path)).resolve()
    rules_text = config.rules_path.read_text(encoding="utf-8")
    entries = get_processable_entries(manifest)

    console.print(f"  [dim]Building work packets for {len(entries)} functions[/]")

    packets: list[WorkPacket] = []
    skipped = 0
    warned = 0

    for rel_path, entry in entries:
        filepath = base_path / rel_path
        if not filepath.exists():
            console.print(f"  [red]✗ File not found: {filepath}[/]")
            continue

        source_text = filepath.read_text(encoding="utf-8")
        source_lines = source_text.splitlines()

        uid = entry["uid"]
        func_name = entry["name"]
        class_name = entry.get("class_name")

        # V1.2 #22: Load custom prompt template if configured
        prompt_template: str | None = None
        if config.prompt_template_path:
            tmpl_path = Path(config.prompt_template_path)
            if tmpl_path.exists():
                prompt_template = tmpl_path.read_text(encoding="utf-8")

        try:
            packet = assemble_work_packet(
                source_text=source_text,
                source_lines=source_lines,
                uid=uid,
                function_name=func_name,
                file_path=rel_path,
                rules_text=rules_text,
                token_budget=config.token_budget,
                header_token_budget=config.header_token_budget,
                class_name=class_name,
                prompt_template=prompt_template,
                token_counter_backend=config.token_counter_backend.value,
            )
        except ValueError as exc:
            console.print(f"  [red]✗ {func_name} ({rel_path}): {exc}[/]")
            continue

        if packet.budget_status == "ABORT":
            entry["status"] = STATUS_SKIPPED_OVERSIZED
            skipped += 1
            console.print(
                f"  [yellow]⚠ SKIPPED {func_name} — {packet.estimated_tokens} tokens "
                f"exceeds 2x budget ({config.token_budget})[/]"
            )
            continue

        if packet.budget_status == "WARN":
            warned += 1
            console.print(
                f"  [yellow]⚠ WARN {func_name} — {packet.estimated_tokens} tokens "
                f"exceeds budget ({config.token_budget}), proceeding anyway[/]"
            )

        if save_packets:
            wp_dir = config.output_dir / "work_packets"
            save_work_packet(packet, wp_dir)

        packets.append(packet)

    console.print(
        f"  [dim]Assembled {len(packets)} work packets "
        f"({skipped} skipped, {warned} warned)[/]"
    )

    if save_packets and packets:
        console.print(f"  [green]✓ Work packets saved to {config.output_dir / 'work_packets'}[/]")

    return packets
