"""Response extractor — extract transformed code from LLM output.

Uses compiled regex to capture the code block between UID anchor markers
in the LLM's response. Handles common LLM output patterns:
    - Code directly between anchors
    - Code wrapped in markdown fences (```python ... ```)
    - Mixed text + code output
"""

from __future__ import annotations

import re


# Primary pattern: code between UID anchors (dotall for multiline)
_UID_BLOCK_RE = re.compile(
    r"#\s*UID:([a-f0-9]{12}):START\s*\n(.*?)#\s*UID:\1:END",
    re.DOTALL,
)

# Fallback: code inside markdown fences
_MARKDOWN_FENCE_RE = re.compile(
    r"```(?:python)?\s*\n(.*?)```",
    re.DOTALL,
)


class ExtractionError(Exception):
    """Raised when the transformed code cannot be extracted from the LLM response."""


def extract_transformed_code(response_text: str, uid: str) -> list[str]:
    """Extract the transformed function code from an LLM response.

    Tries multiple strategies in order:
        1. Exact UID anchor match (preferred — markers preserved)
        2. Markdown fence extraction (fallback)
        3. Raw response as-is (last resort)

    Args:
        response_text: The complete LLM response text.
        uid: The expected UID (12 hex chars) to match against.

    Returns:
        The extracted code as a list of lines (no trailing newlines).

    Raises:
        ExtractionError: If no code block can be identified in the response.
    """
    if not response_text or not response_text.strip():
        msg = "LLM returned an empty response"
        raise ExtractionError(msg)

    # Strategy 1: UID anchor match
    match = _UID_BLOCK_RE.search(response_text)
    if match and match.group(1) == uid:
        code = match.group(2).strip()
        if code:
            return code.splitlines()

    # Strategy 2: Any UID anchor match (LLM may have mangled the UID)
    match = _UID_BLOCK_RE.search(response_text)
    if match:
        code = match.group(2).strip()
        if code:
            return code.splitlines()

    # Strategy 3: Markdown fence
    fence_match = _MARKDOWN_FENCE_RE.search(response_text)
    if fence_match:
        code = fence_match.group(1).strip()
        if code:
            # Check if the code contains the function definition
            lines = code.splitlines()
            # Remove any anchor markers that might be inside the fence
            cleaned: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("# UID:") and (":START" in stripped or ":END" in stripped):
                    continue
                cleaned.append(line)
            if cleaned:
                return cleaned

    # Strategy 4: Raw response — only if it looks like Python code
    text = response_text.strip()
    lines = text.splitlines()
    # Filter out obvious non-code lines
    code_lines: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        # Skip UID markers
        if stripped.startswith("# UID:") and (":START" in stripped or ":END" in stripped):
            continue
        # Skip markdown fences
        if stripped.startswith("```"):
            continue
        # Start capturing once we see a def/async def
        if stripped.startswith(("def ", "async def ", "@")):
            in_code = True
        if in_code:
            code_lines.append(line)

    if code_lines:
        return code_lines

    msg = (
        f"Could not extract transformed code from LLM response. "
        f"Expected UID anchors for '{uid}' or a markdown code fence. "
        f"Response preview: {response_text[:200]!r}"
    )
    raise ExtractionError(msg)
