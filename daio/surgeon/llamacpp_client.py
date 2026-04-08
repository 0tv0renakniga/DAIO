"""Llama.cpp GGUF client — direct local inference without server dependency.

V2.0 Feature #21: Direct integration with llama-cpp-python for
single-process, serverless inference. Same dispatch() interface as
ollama_client.py for drop-in swapping.

Advantages over Ollama:
    - No server process required
    - Direct control over context window, sampling, and GPU layers
    - GGUF model loading with automatic GPU offloading
    - Lower latency (no HTTP overhead)

Usage:
    from daio.surgeon.llamacpp_client import dispatch
    response = dispatch(prompt, model_path="/path/to/model.gguf")
"""

from __future__ import annotations


class LlamaCppError(Exception):
    """Raised when llama-cpp-python inference fails."""


def dispatch(
    prompt: str,
    model_path: str,
    *,
    n_ctx: int = 8192,
    n_gpu_layers: int = 0,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    timeout: int = 600,
) -> str:
    """Send a prompt to a local GGUF model and return the response.

    Lazy-loads the model on first call and caches it for subsequent calls.
    Thread-safe via the GIL (single-threaded pipeline design).

    Args:
        prompt: The complete work packet text to send.
        model_path: Absolute path to the GGUF model file.
        n_ctx: Context window size in tokens.
        n_gpu_layers: Number of layers to offload to GPU (0 = CPU only).
        temperature: Sampling temperature (low = deterministic).
        max_tokens: Maximum tokens to generate.
        timeout: Not used directly (llama.cpp is synchronous), kept for API parity.

    Returns:
        The model's complete response text.

    Raises:
        LlamaCppError: If the model fails to load or inference fails.
        ImportError: If llama-cpp-python is not installed.
    """
    try:
        from llama_cpp import Llama  # noqa: F811
    except ImportError as exc:
        msg = (
            "llama-cpp-python is required for direct GGUF inference. "
            "Install with: uv add llama-cpp-python"
        )
        raise ImportError(msg) from exc

    # Get or create cached model instance
    model = _get_model(model_path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers)

    try:
        output = model(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=["# UID:", "```\n\n", "==="],
            echo=False,
        )
    except Exception as exc:
        msg = f"Llama.cpp inference failed: {exc}"
        raise LlamaCppError(msg) from exc

    choices = output.get("choices", [])
    if not choices:
        msg = "Llama.cpp returned no output choices"
        raise LlamaCppError(msg)

    return choices[0].get("text", "")


# ---------------------------------------------------------------------------
# Model cache — one model loaded per process lifetime
# ---------------------------------------------------------------------------

_model_cache: dict[str, object] = {}


def _get_model(
    model_path: str,
    *,
    n_ctx: int = 8192,
    n_gpu_layers: int = 0,
) -> object:
    """Lazy-load and cache a GGUF model.

    Args:
        model_path: Path to the GGUF file.
        n_ctx: Context window size.
        n_gpu_layers: GPU layer count.

    Returns:
        Llama model instance.

    Raises:
        LlamaCppError: If model file not found or loading fails.
    """
    cache_key = f"{model_path}:{n_ctx}:{n_gpu_layers}"
    if cache_key in _model_cache:
        return _model_cache[cache_key]

    from pathlib import Path

    if not Path(model_path).exists():
        msg = f"GGUF model not found: {model_path}"
        raise LlamaCppError(msg)

    try:
        from llama_cpp import Llama  # noqa: F811

        model = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
    except Exception as exc:
        msg = f"Failed to load GGUF model '{model_path}': {exc}"
        raise LlamaCppError(msg) from exc

    _model_cache[cache_key] = model
    return model
