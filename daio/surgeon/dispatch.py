"""Backend-aware dispatch wrapper for Surgeon.

Routes prompt dispatch to either:
    - Ollama HTTP backend
    - llama.cpp CLI backend (llama-cli subprocess)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from daio.config import BackendMode, DAIOConfig
from daio.surgeon.ollama_client import OllamaError, dispatch as ollama_dispatch


class DispatchError(Exception):
    """Raised when backend dispatch fails."""


def _build_llamacpp_command(config: DAIOConfig) -> list[str]:
    """Build the llama-cli command from config fields."""
    if config.gguf_model_path is None:
        msg = "gguf_model_path is required for llamacpp backend"
        raise DispatchError(msg)

    model_path = Path(config.gguf_model_path)
    if not model_path.exists():
        msg = f"GGUF model not found: {model_path}"
        raise DispatchError(msg)

    cmd = [
        "llama-cli",
        "-m", str(model_path),
        "-c", str(config.n_ctx),
        "-ngl", str(config.n_gpu_layers),
        "-n", str(config.n_predict),
        "--temp", str(config.temperature),
        "-fa", config.flash_attn.value,
    ]

    if config.n_threads is not None:
        cmd.extend(["-t", str(config.n_threads)])

    cmd.append("--mmap" if config.mmap else "--no-mmap")
    if config.mlock:
        cmd.append("--mlock")

    # Always keep CLI output machine-readable.
    cmd.extend(["--log-disable", "--simple-io"])
    return cmd


def dispatch(prompt: str, config: DAIOConfig) -> str:
    """Dispatch prompt to selected backend and return response text."""
    if config.backend == BackendMode.OLLAMA:
        try:
            return ollama_dispatch(
                prompt=prompt,
                model=config.model,
                ollama_url=config.ollama_url,
                timeout=config.request_timeout,
            )
        except OllamaError as exc:
            raise DispatchError(str(exc)) from exc

    if config.backend == BackendMode.LLAMACPP:
        cmd = _build_llamacpp_command(config)
        cmd.extend(["-p", prompt])
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.request_timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            msg = "llama-cli not found in PATH"
            raise DispatchError(msg) from exc
        except subprocess.TimeoutExpired as exc:
            msg = f"llama-cli timed out after {config.request_timeout}s"
            raise DispatchError(msg) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            msg = f"llama-cli exited with code {result.returncode}: {stderr[:500]}"
            raise DispatchError(msg)
        return (result.stdout or "").strip()

    msg = f"Unsupported backend: {config.backend}"
    raise DispatchError(msg)

