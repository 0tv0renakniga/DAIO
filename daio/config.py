"""DAIO configuration schema and loader.

Uses Pydantic v2 for strict validation. The config.yaml file is the single
source of truth for all runtime behavior — no CLI flag should override
without explicit precedence logic.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ScopeMode(str, Enum):
    """How much of the target codebase to process."""

    FULL = "full"
    MODULE = "module"
    FILELIST = "filelist"


class BackendMode(str, Enum):
    """LLM backend selection."""

    OLLAMA = "ollama"
    LLAMACPP = "llamacpp"


class FlashAttnMode(str, Enum):
    """llama.cpp flash attention toggle."""

    ON = "on"
    OFF = "off"
    AUTO = "auto"


class SASTTool(str, Enum):
    """Supported SAST backends."""

    BANDIT = "bandit"
    SEMGREP = "semgrep"


class TypeCheckerTool(str, Enum):
    """Supported type-check backends."""

    MYPY = "mypy"
    PYRIGHT = "pyright"


class TokenCounterBackend(str, Enum):
    """Supported token counting backends."""

    HEURISTIC = "heuristic"
    TIKTOKEN = "tiktoken"


class DAIOConfig(BaseModel):
    """Root configuration schema for the DAIO pipeline.

    All fields have sane defaults where possible. Required fields:
    - model: Ollama model identifier
    - target_path: path to target codebase
    - rules_path: path to rules.md

    Attributes:
        model: Ollama-compatible model name (e.g., 'qwen2.5-coder:7b-instruct-q8_0').
        ollama_url: Base URL for the Ollama API.
        target_path: Absolute or relative path to the target codebase.
        rules_path: Path to the rules.md instruction file.
        scope: Processing scope — full codebase, single module, or explicit file list.
        file_list: Explicit list of files when scope is 'filelist'.
        token_budget: Maximum token estimate for a work packet (chars / 4 heuristic).
        header_token_budget: Maximum tokens allocated to the global header section.
        max_retries: Number of LLM retry attempts on validation failure.
        auto_commit: Whether to git-commit after each successfully refactored function.
        ruff_config: Optional path to a ruff configuration file.
        request_timeout: HTTP timeout in seconds for Ollama requests (CPU inference is slow).
        loc_shrink_floor: Minimum ratio of output LOC to input LOC (hallucination guard).
        loc_growth_ceiling: Maximum ratio of output LOC to input LOC (hallucination guard).
        output_dir: Directory for pipeline artifacts (manifest, audit log, work packets).

    Raises:
        ValueError: If file_list is missing when scope is 'filelist'.
        ValueError: If target_path does not exist.
        ValueError: If rules_path does not exist.

    Examples:
        >>> config = DAIOConfig(
        ...     model="qwen2.5-coder:7b-instruct-q8_0",
        ...     target_path="/path/to/project",
        ...     rules_path="/path/to/rules.md",
        ... )
    """

    # --- LLM Configuration ---
    model: str = Field(
        ...,
        description="Ollama-compatible model name (e.g., 'qwen2.5-coder:7b-instruct-q8_0')",
    )
    ollama_url: str = Field(
        default="http://localhost:11434",
        description="Base URL for Ollama API",
    )
    backend: BackendMode = Field(
        default=BackendMode.OLLAMA,
        description="LLM backend: 'ollama' or 'llamacpp'",
    )

    # --- llama.cpp Runtime ---
    gguf_model_path: Optional[Path] = Field(
        default=None,
        description="Path to GGUF model file for llama.cpp backend (-m/--model)",
    )
    n_ctx: int = Field(
        default=8192,
        ge=256,
        le=131072,
        description="llama.cpp context size (-c/--ctx-size)",
    )
    n_gpu_layers: int | str = Field(
        default=0,
        description="llama.cpp GPU layers (-ngl/--n-gpu-layers): int, 'auto', or 'all'",
    )
    n_threads: Optional[int] = Field(
        default=None,
        ge=1,
        le=512,
        description="llama.cpp thread count (-t/--threads), unset lets runtime decide",
    )
    n_predict: int = Field(
        default=4096,
        ge=-1,
        le=1048576,
        description="llama.cpp max tokens (-n/--predict), -1 means infinity",
    )
    temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (--temp/--temperature)",
    )
    flash_attn: FlashAttnMode = Field(
        default=FlashAttnMode.AUTO,
        description="llama.cpp flash attention mode (-fa/--flash-attn): on/off/auto",
    )
    mmap: bool = Field(
        default=True,
        description="llama.cpp model mmap toggle (--mmap/--no-mmap)",
    )
    mlock: bool = Field(
        default=False,
        description="llama.cpp memory lock toggle (--mlock)",
    )

    # --- Target Configuration ---
    target_path: Path = Field(
        ...,
        description="Path to the target codebase root",
    )
    rules_path: Path = Field(
        ...,
        description="Path to the rules.md instruction file",
    )
    scope: ScopeMode = Field(
        default=ScopeMode.FULL,
        description="Processing scope: 'full', 'module', or 'filelist'",
    )
    file_list: Optional[list[str]] = Field(
        default=None,
        description="Explicit file list when scope is 'filelist'",
    )

    # --- Token Budget ---
    token_budget: int = Field(
        default=4096,
        ge=1024,
        le=32768,
        description="Max tokens for work packet (chars / 4 heuristic)",
    )
    header_token_budget: int = Field(
        default=512,
        ge=128,
        le=4096,
        description="Max tokens for global header in work packet",
    )
    prompt_template_path: str | None = Field(
        default=None,
        description="Path to custom prompt template file (V1.2 #22)",
    )

    # --- Retry & Validation ---
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max LLM retry attempts on validation failure",
    )
    loc_shrink_floor: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Min ratio output_LOC / input_LOC (below = hallucination)",
    )
    loc_growth_ceiling: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="Max ratio output_LOC / input_LOC (above = hallucination)",
    )

    # --- Git & Commit ---
    auto_commit: bool = Field(
        default=True,
        description="Git-commit after each successfully refactored function",
    )

    # --- Infrastructure ---
    ruff_config: Optional[Path] = Field(
        default=None,
        description="Optional path to ruff configuration file",
    )
    request_timeout: int = Field(
        default=600,
        ge=30,
        le=3600,
        description="HTTP timeout in seconds for Ollama (CPU inference can be slow)",
    )
    enable_sast: bool = Field(
        default=False,
        description="Enable SAST validation gate",
    )
    sast_tool: SASTTool = Field(
        default=SASTTool.BANDIT,
        description="SAST tool backend: 'bandit' or 'semgrep'",
    )
    enable_typecheck: bool = Field(
        default=False,
        description="Enable type-check validation gate",
    )
    type_checker: TypeCheckerTool = Field(
        default=TypeCheckerTool.MYPY,
        description="Type checker backend: 'mypy' or 'pyright'",
    )
    token_counter_backend: TokenCounterBackend = Field(
        default=TokenCounterBackend.HEURISTIC,
        description="Token counting backend: 'heuristic' or 'tiktoken'",
    )
    dataset_export_enabled: bool = Field(
        default=False,
        description="Enable JSONL fine-tuning dataset export for successful transforms",
    )
    dataset_output_path: Path = Field(
        default=Path(".daio/training_dataset.jsonl"),
        description="Output path for JSONL dataset export",
    )
    output_dir: Path = Field(
        default=Path(".daio"),
        description="Directory for pipeline artifacts (manifest, audit log, etc.)",
    )

    # --- Validators ---

    @field_validator("target_path")
    @classmethod
    def target_path_must_exist(cls, v: Path) -> Path:
        """Validate that the target codebase path exists.

        Args:
            v: The path to validate.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path does not exist.
        """
        resolved = v.resolve()
        if not resolved.exists():
            msg = f"target_path does not exist: {resolved}"
            raise ValueError(msg)
        return resolved

    @field_validator("rules_path")
    @classmethod
    def rules_path_must_exist(cls, v: Path) -> Path:
        """Validate that the rules file exists.

        Args:
            v: The path to validate.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path does not exist.
        """
        resolved = v.resolve()
        if not resolved.exists():
            msg = f"rules_path does not exist: {resolved}"
            raise ValueError(msg)
        return resolved

    @field_validator("ruff_config")
    @classmethod
    def ruff_config_must_exist_if_set(cls, v: Optional[Path]) -> Optional[Path]:
        """Validate ruff config path exists if provided.

        Args:
            v: Optional path to ruff config.

        Returns:
            Resolved path or None.

        Raises:
            ValueError: If the path is set but does not exist.
        """
        if v is not None:
            resolved = v.resolve()
            if not resolved.exists():
                msg = f"ruff_config does not exist: {resolved}"
                raise ValueError(msg)
            return resolved
        return v

    @field_validator("gguf_model_path")
    @classmethod
    def gguf_model_path_must_exist_if_set(cls, v: Optional[Path]) -> Optional[Path]:
        """Validate GGUF model path exists if provided."""
        if v is not None:
            resolved = v.resolve()
            if not resolved.exists():
                msg = f"gguf_model_path does not exist: {resolved}"
                raise ValueError(msg)
            return resolved
        return v

    @field_validator("n_gpu_layers")
    @classmethod
    def validate_n_gpu_layers(cls, v: int | str) -> int | str:
        """Allow int GPU layers or special string values."""
        if isinstance(v, int):
            if v < -1:
                msg = "n_gpu_layers must be >= -1, or one of: 'auto', 'all'"
                raise ValueError(msg)
            return v
        if isinstance(v, str):
            lowered = v.lower().strip()
            if lowered in {"auto", "all"}:
                return lowered
        msg = "n_gpu_layers must be an int, 'auto', or 'all'"
        raise ValueError(msg)

    @model_validator(mode="after")
    def filelist_required_when_scope_is_filelist(self) -> "DAIOConfig":
        """Ensure file_list is populated when scope is 'filelist'.

        Returns:
            Self after validation.

        Raises:
            ValueError: If scope is 'filelist' but file_list is empty or None.
        """
        if self.scope == ScopeMode.FILELIST:
            if not self.file_list:
                msg = "file_list must be provided when scope is 'filelist'"
                raise ValueError(msg)
        if self.backend == BackendMode.LLAMACPP and self.gguf_model_path is None:
            msg = "gguf_model_path must be provided when backend is 'llamacpp'"
            raise ValueError(msg)
        return self


def load_config(config_path: Path) -> DAIOConfig:
    """Load and validate a DAIO config from a YAML file.

    Args:
        config_path: Path to the config.yaml file.

    Returns:
        Validated DAIOConfig instance.

    Raises:
        FileNotFoundError: If config_path does not exist.
        yaml.YAMLError: If the file is not valid YAML.
        pydantic.ValidationError: If the config fails schema validation.

    Examples:
        >>> config = load_config(Path("config.yaml"))
        >>> print(config.model)
        'qwen2.5-coder:7b-instruct-q8_0'
    """
    config_path = config_path.resolve()
    if not config_path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        msg = f"Config file must contain a YAML mapping, got: {type(raw).__name__}"
        raise TypeError(msg)

    return DAIOConfig(**raw)
