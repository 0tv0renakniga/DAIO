"""Work packet assembler — stitch together rules, header, snippet, and instruction.

A work packet is the complete prompt bundle sent to the LLM. It contains:
    1. RULES — the refactoring instructions from rules.md
    2. GLOBAL CONTEXT — filtered imports and constants
    3. TARGET FUNCTION — the code between UID anchors
    4. INSTRUCTION — the transformation directive

The assembler enforces the token budget and flags oversized packets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daio.sieve.header import build_global_header
from daio.sieve.snippet import extract_by_uid
from daio.sieve.token_counter import check_budget, estimate_tokens


# Work packet template
_PACKET_TEMPLATE = """\
=== RULES ===
{rules}

=== GLOBAL CONTEXT ===
{header}

=== TARGET FUNCTION ===
# UID:{uid}:START
{snippet}
# UID:{uid}:END

=== INSTRUCTION ===
Transform the function between UID markers according to the RULES above.
Return ONLY the transformed function, preserving the UID markers exactly.
Do NOT add, remove, or reorder imports. Do NOT modify other functions.
"""


@dataclass
class WorkPacket:
    """A complete prompt bundle for a single function transformation.

    Attributes:
        uid: The function's UID.
        function_name: Name of the target function.
        file_path: Relative path to the source file.
        packet_text: The assembled prompt text.
        snippet_lines: The raw snippet lines (for LOC comparison after transform).
        estimated_tokens: chars/4 token estimate of the full packet.
        budget_status: 'OK', 'WARN', or 'ABORT'.
    """

    uid: str
    function_name: str
    file_path: str
    packet_text: str
    snippet_lines: list[str]
    estimated_tokens: int
    budget_status: str


def assemble_work_packet(
    source_text: str,
    source_lines: list[str],
    uid: str,
    function_name: str,
    file_path: str,
    rules_text: str,
    token_budget: int = 4096,
    header_token_budget: int = 512,
) -> WorkPacket:
    """Assemble a complete work packet for a single function.

    Args:
        source_text: Full source file as a string.
        source_lines: Full source file as a list of lines.
        uid: The function's UID (12 hex chars).
        function_name: Name of the function being processed.
        file_path: Relative path to the source file (for metadata).
        rules_text: Contents of the rules.md file.
        token_budget: Maximum token budget for the entire work packet.
        header_token_budget: Maximum token budget for the global header.

    Returns:
        Assembled WorkPacket with budget status.

    Raises:
        ValueError: If UID anchors are not found in source.
    """
    # Extract the snippet
    snippet_lines, _, _ = extract_by_uid(source_lines, uid)
    snippet_text = "\n".join(snippet_lines)

    # Build the pruned global header
    header = build_global_header(
        source_text, snippet_lines, header_token_budget
    )

    # Assemble the packet
    packet_text = _PACKET_TEMPLATE.format(
        rules=rules_text.strip(),
        header=header,
        uid=uid,
        snippet=snippet_text,
    )

    # Check token budget
    tokens = estimate_tokens(packet_text)
    status = check_budget(tokens, token_budget)

    return WorkPacket(
        uid=uid,
        function_name=function_name,
        file_path=file_path,
        packet_text=packet_text,
        snippet_lines=snippet_lines,
        estimated_tokens=tokens,
        budget_status=status,
    )


def save_work_packet(packet: WorkPacket, output_dir: Path) -> Path:
    """Save a work packet to disk for inspection or dry-run.

    Args:
        packet: The assembled work packet.
        output_dir: Directory to save into.

    Returns:
        Path to the saved work packet file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"wp_{packet.uid}_{packet.function_name}.txt"
    path = output_dir / filename
    path.write_text(packet.packet_text, encoding="utf-8")
    return path
