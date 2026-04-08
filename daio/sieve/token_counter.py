"""Token counter — pluggable token estimation for work packet budgeting.

V2.0 Fix #7: Pluggable backend system.
    - 'heuristic' (default): chars/4 approximation, zero dependencies
    - 'tiktoken': BPE-accurate counting via tiktoken library (optional dep)

The backend is selectable per call. The heuristic is conservative for
code (underestimates by ~15-20%) but avoids extra dependencies.
"""

from __future__ import annotations

from typing import Literal

# Lazy-loaded tiktoken module (avoid import cost if not used)
_tiktoken_encoding = None


def _get_tiktoken_encoding():
    """Lazy-load tiktoken with cl100k_base encoding.

    Returns:
        tiktoken Encoding object.

    Raises:
        ImportError: If tiktoken is not installed.
    """
    global _tiktoken_encoding
    if _tiktoken_encoding is None:
        try:
            import tiktoken
        except ImportError as exc:
            msg = (
                "tiktoken is required for accurate token counting. "
                "Install with: uv add tiktoken"
            )
            raise ImportError(msg) from exc
        _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
    return _tiktoken_encoding


def estimate_tokens(
    text: str,
    backend: Literal["heuristic", "tiktoken"] = "heuristic",
) -> int:
    """Estimate the number of tokens in a text string.

    Args:
        text: The text to estimate tokens for.
        backend: Token counting backend.
            - 'heuristic': chars/4 (fast, zero deps, ~85% accurate for code)
            - 'tiktoken': BPE tokenizer (accurate, requires tiktoken)

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

    if backend == "tiktoken":
        enc = _get_tiktoken_encoding()
        return len(enc.encode(text))

    # Default: chars/4 heuristic
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

