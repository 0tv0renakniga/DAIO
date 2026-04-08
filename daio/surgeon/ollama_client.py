"""Ollama HTTP client — generic, stateless LLM dispatch.

A pure function wrapper: prompt text in → response text out.
The Orchestrator controls all context; the LLM has zero filesystem
authority and zero memory of prior tasks.

Supports any Ollama-compatible model via config. Handles streaming
responses and timeout for CPU inference (which can be very slow).
"""

from __future__ import annotations

import httpx


class OllamaError(Exception):
    """Raised when the Ollama API returns an error or is unreachable."""


def dispatch(
    prompt: str,
    model: str,
    *,
    ollama_url: str = "http://localhost:11434",
    timeout: int = 600,
) -> str:
    """Send a prompt to Ollama and return the complete response text.

    This is a synchronous, blocking call. For CPU inference on large
    models, this can take several minutes — hence the generous default
    timeout.

    Args:
        prompt: The complete work packet text to send.
        model: Ollama model identifier (e.g., 'qwen2.5-coder:7b-instruct-q8_0').
        ollama_url: Base URL of the Ollama API server.
        timeout: HTTP request timeout in seconds.

    Returns:
        The LLM's complete response text.

    Raises:
        OllamaError: If the API is unreachable, returns an error, or times out.
    """
    url = f"{ollama_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,  # Low temp for deterministic code transforms
            "num_predict": 4096,  # Max tokens to generate
        },
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
    except httpx.ConnectError as exc:
        msg = (
            f"Cannot connect to Ollama at {ollama_url}. "
            "Start with: ollama serve"
        )
        raise OllamaError(msg) from exc
    except httpx.TimeoutException as exc:
        msg = (
            f"Ollama request timed out after {timeout}s. "
            "Consider increasing request_timeout in config.yaml or using a smaller model."
        )
        raise OllamaError(msg) from exc
    except httpx.HTTPError as exc:
        msg = f"HTTP error communicating with Ollama: {exc}"
        raise OllamaError(msg) from exc

    if response.status_code != 200:
        msg = f"Ollama returned status {response.status_code}: {response.text[:500]}"
        raise OllamaError(msg)

    try:
        data = response.json()
    except ValueError as exc:
        msg = f"Ollama returned non-JSON response: {response.text[:500]}"
        raise OllamaError(msg) from exc

    return data.get("response", "")
