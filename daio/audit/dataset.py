"""Dataset exporter — generate fine-tuning datasets from successful refactors.

V2.0 Feature #20: Every successful refactor that passes all validation
gates is serialized as a JSONL pair for supervised fine-tuning:
    {"instruction": "<work_packet>", "output": "<transformed_code>"}

This enables running DAIO with a large model (e.g., GPT-4o) overnight
to generate thousands of verified refactoring examples, then fine-tuning
a fast, local 3B parameter model to replicate the same transformations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def export_training_pair(
    output_path: Path,
    work_packet_text: str,
    transformed_code: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a single instruction/output pair to the dataset file.

    Args:
        output_path: Path to the output JSONL file.
        work_packet_text: The complete work packet (instruction).
        transformed_code: The validated, transformed function (output).
        metadata: Optional metadata dict (uid, function_name, etc.).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict[str, Any] = {
        "instruction": work_packet_text,
        "output": transformed_code,
    }
    if metadata:
        entry["metadata"] = metadata

    with output_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_dataset(dataset_path: Path) -> list[dict[str, Any]]:
    """Load a JSONL fine-tuning dataset.

    Args:
        dataset_path: Path to the JSONL file.

    Returns:
        List of instruction/output dicts.

    Raises:
        FileNotFoundError: If the dataset file does not exist.
    """
    if not dataset_path.exists():
        msg = f"Dataset file not found: {dataset_path}"
        raise FileNotFoundError(msg)

    entries: list[dict[str, Any]] = []
    for line in dataset_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def get_dataset_stats(dataset_path: Path) -> dict[str, Any]:
    """Get statistics for a fine-tuning dataset.

    Args:
        dataset_path: Path to the JSONL file.

    Returns:
        Dict with total_pairs, avg_instruction_tokens, avg_output_tokens.
    """
    if not dataset_path.exists():
        return {"total_pairs": 0}

    entries = load_dataset(dataset_path)
    if not entries:
        return {"total_pairs": 0}

    total = len(entries)
    avg_inst_len = sum(len(e.get("instruction", "")) for e in entries) // total
    avg_out_len = sum(len(e.get("output", "")) for e in entries) // total

    return {
        "total_pairs": total,
        "avg_instruction_chars": avg_inst_len,
        "avg_output_chars": avg_out_len,
        "avg_instruction_tokens_est": avg_inst_len // 4,
        "avg_output_tokens_est": avg_out_len // 4,
    }
