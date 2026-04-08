"""Token counter — estimate token counts for work packet budgeting.

Uses a chars/4 heuristic by default. This is a reasonable approximation
for most LLM tokenizers and avoids requiring model-specific tokenizer
libraries. A configurable safety margin accounts for the heuristic's
imprecision.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses the chars/4 heuristic, which is a reasonable approximation
    for English text across most tokenizers (GPT, LLaMA, Qwen).

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (integer, always >= 0).

    Examples:
        >>> estimate_tokens("Hello, world!")
        3
        >>> estimate_tokens("")
        0
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def check_budget(
    estimated_tokens: int,
    token_budget: int,
) -> str:
    """Check if estimated tokens are within budget.

    Args:
        estimated_tokens: The estimated token count.
        token_budget: The configured maximum token budget.

    Returns:
        Status string: "OK", "WARN" (over budget but < 2x), or "ABORT" (>= 2x budget).

    Examples:
        >>> check_budget(3000, 4096)
        'OK'
        >>> check_budget(5000, 4096)
        'WARN'
        >>> check_budget(9000, 4096)
        'ABORT'
    """
    if estimated_tokens <= token_budget:
        return "OK"
    if estimated_tokens < token_budget * 2:
        return "WARN"
    return "ABORT"
