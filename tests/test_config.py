"""Tests for daio.config — schema validation and loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from daio.config import DAIOConfig, ScopeMode, load_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CONFIG = {
    "model": "test-model:7b-q8_0",
    "target_path": ".",  # current dir always exists
    "rules_path": None,  # will be patched per-test
}


@pytest.fixture()
def tmp_rules(tmp_path: Path) -> Path:
    """Create a temporary rules.md file.

    Args:
        tmp_path: pytest tmp_path fixture.

    Returns:
        Path to the temporary rules.md file.
    """
    rules = tmp_path / "rules.md"
    rules.write_text("# Test rules\nAdd docstrings.", encoding="utf-8")
    return rules


@pytest.fixture()
def valid_config_dict(tmp_rules: Path) -> dict:
    """Build a valid config dict with real paths.

    Args:
        tmp_rules: Path to temporary rules.md.

    Returns:
        Config dict with valid target_path and rules_path.
    """
    return {
        "model": "test-model:7b-q8_0",
        "target_path": str(tmp_rules.parent),
        "rules_path": str(tmp_rules),
    }


@pytest.fixture()
def config_yaml(tmp_path: Path, valid_config_dict: dict) -> Path:
    """Write a valid config.yaml to disk.

    Args:
        tmp_path: pytest tmp_path fixture.
        valid_config_dict: Dict to serialize.

    Returns:
        Path to the config.yaml file.
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(valid_config_dict), encoding="utf-8")
    return config_file


# ---------------------------------------------------------------------------
# Happy Path Tests
# ---------------------------------------------------------------------------


class TestDAIOConfigValid:
    """Tests for valid configuration construction."""

    def test_minimal_valid_config(self, valid_config_dict: dict) -> None:
        """Minimal config with only required fields should pass validation."""
        config = DAIOConfig(**valid_config_dict)
        assert config.model == "test-model:7b-q8_0"
        assert config.token_budget == 4096  # default
        assert config.max_retries == 3  # default
        assert config.auto_commit is True  # default

    def test_defaults_populated(self, valid_config_dict: dict) -> None:
        """All optional fields should have sane defaults."""
        config = DAIOConfig(**valid_config_dict)
        assert config.ollama_url == "http://localhost:11434"
        assert config.scope == ScopeMode.FULL
        assert config.header_token_budget == 512
        assert config.loc_shrink_floor == 0.3
        assert config.loc_growth_ceiling == 3.0
        assert config.request_timeout == 600
        assert config.output_dir == Path(".daio")

    def test_scope_filelist_with_files(self, valid_config_dict: dict) -> None:
        """scope='filelist' with file_list should pass."""
        valid_config_dict["scope"] = "filelist"
        valid_config_dict["file_list"] = ["file_a.py", "file_b.py"]
        config = DAIOConfig(**valid_config_dict)
        assert config.scope == ScopeMode.FILELIST
        assert len(config.file_list) == 2

    def test_custom_token_budget(self, valid_config_dict: dict) -> None:
        """Custom token budget within bounds."""
        valid_config_dict["token_budget"] = 8192
        config = DAIOConfig(**valid_config_dict)
        assert config.token_budget == 8192

    def test_auto_commit_disabled(self, valid_config_dict: dict) -> None:
        """auto_commit can be toggled off."""
        valid_config_dict["auto_commit"] = False
        config = DAIOConfig(**valid_config_dict)
        assert config.auto_commit is False

    def test_target_path_resolved(self, valid_config_dict: dict) -> None:
        """target_path should be resolved to absolute."""
        config = DAIOConfig(**valid_config_dict)
        assert config.target_path.is_absolute()

    def test_backend_llamacpp_with_model_path(self, valid_config_dict: dict, tmp_path: Path) -> None:
        """llamacpp backend should accept a valid gguf_model_path."""
        gguf = tmp_path / "model.gguf"
        gguf.write_text("mock-gguf", encoding="utf-8")
        valid_config_dict["backend"] = "llamacpp"
        valid_config_dict["gguf_model_path"] = str(gguf)
        config = DAIOConfig(**valid_config_dict)
        assert config.backend.value == "llamacpp"
        assert config.gguf_model_path == gguf.resolve()

    def test_n_gpu_layers_accepts_int(self, valid_config_dict: dict) -> None:
        """n_gpu_layers should accept integer values."""
        valid_config_dict["n_gpu_layers"] = 32
        config = DAIOConfig(**valid_config_dict)
        assert config.n_gpu_layers == 32

    def test_n_gpu_layers_accepts_auto(self, valid_config_dict: dict) -> None:
        """n_gpu_layers should accept the 'auto' keyword."""
        valid_config_dict["n_gpu_layers"] = "auto"
        config = DAIOConfig(**valid_config_dict)
        assert config.n_gpu_layers == "auto"

    def test_n_gpu_layers_accepts_all(self, valid_config_dict: dict) -> None:
        """n_gpu_layers should accept the 'all' keyword."""
        valid_config_dict["n_gpu_layers"] = "all"
        config = DAIOConfig(**valid_config_dict)
        assert config.n_gpu_layers == "all"

    def test_phase_c_fields_parse(self, valid_config_dict: dict, tmp_path: Path) -> None:
        """Phase C config fields should parse with explicit values."""
        gguf = tmp_path / "phase_c_model.gguf"
        gguf.write_text("mock", encoding="utf-8")
        dataset_path = tmp_path / "dataset.jsonl"

        valid_config_dict.update(
            {
                "backend": "llamacpp",
                "gguf_model_path": str(gguf),
                "n_ctx": 16384,
                "n_gpu_layers": 16,
                "n_threads": 8,
                "n_predict": -1,
                "temperature": 0.2,
                "flash_attn": "on",
                "mmap": False,
                "mlock": True,
                "enable_sast": True,
                "sast_tool": "bandit",
                "enable_typecheck": True,
                "type_checker": "pyright",
                "token_counter_backend": "tiktoken",
                "dataset_export_enabled": True,
                "dataset_output_path": str(dataset_path),
            }
        )
        config = DAIOConfig(**valid_config_dict)
        assert config.backend.value == "llamacpp"
        assert config.n_predict == -1
        assert config.flash_attn.value == "on"
        assert config.enable_sast is True
        assert config.enable_typecheck is True
        assert config.token_counter_backend.value == "tiktoken"


# ---------------------------------------------------------------------------
# Sad Path Tests — Edge Cases & Adversarial Inputs
# ---------------------------------------------------------------------------


class TestDAIOConfigInvalid:
    """Tests for invalid / adversarial configurations."""

    def test_missing_model_raises(self, tmp_rules: Path) -> None:
        """Missing required 'model' field should raise ValidationError."""
        with pytest.raises(Exception):  # pydantic.ValidationError
            DAIOConfig(
                target_path=str(tmp_rules.parent),
                rules_path=str(tmp_rules),
            )

    def test_missing_target_path_raises(self, tmp_rules: Path) -> None:
        """Missing required 'target_path' should raise."""
        with pytest.raises(Exception):
            DAIOConfig(
                model="test-model",
                rules_path=str(tmp_rules),
            )

    def test_nonexistent_target_path_raises(self, tmp_rules: Path) -> None:
        """target_path pointing to nonexistent dir should raise."""
        with pytest.raises(ValueError, match="target_path does not exist"):
            DAIOConfig(
                model="test-model",
                target_path="/nonexistent/path/that/should/never/exist",
                rules_path=str(tmp_rules),
            )

    def test_nonexistent_rules_path_raises(self, tmp_rules: Path) -> None:
        """rules_path pointing to nonexistent file should raise."""
        with pytest.raises(ValueError, match="rules_path does not exist"):
            DAIOConfig(
                model="test-model",
                target_path=str(tmp_rules.parent),
                rules_path="/nonexistent/rules.md",
            )

    def test_scope_filelist_without_files_raises(self, valid_config_dict: dict) -> None:
        """scope='filelist' with no file_list should raise."""
        valid_config_dict["scope"] = "filelist"
        with pytest.raises(ValueError, match="file_list must be provided"):
            DAIOConfig(**valid_config_dict)

    def test_scope_filelist_empty_list_raises(self, valid_config_dict: dict) -> None:
        """scope='filelist' with empty file_list should raise."""
        valid_config_dict["scope"] = "filelist"
        valid_config_dict["file_list"] = []
        with pytest.raises(ValueError, match="file_list must be provided"):
            DAIOConfig(**valid_config_dict)

    def test_token_budget_too_small_raises(self, valid_config_dict: dict) -> None:
        """token_budget below minimum (1024) should raise."""
        valid_config_dict["token_budget"] = 100
        with pytest.raises(Exception):
            DAIOConfig(**valid_config_dict)

    def test_token_budget_too_large_raises(self, valid_config_dict: dict) -> None:
        """token_budget above maximum (32768) should raise."""
        valid_config_dict["token_budget"] = 100000
        with pytest.raises(Exception):
            DAIOConfig(**valid_config_dict)

    def test_max_retries_zero_raises(self, valid_config_dict: dict) -> None:
        """max_retries must be at least 1."""
        valid_config_dict["max_retries"] = 0
        with pytest.raises(Exception):
            DAIOConfig(**valid_config_dict)

    def test_nonexistent_ruff_config_raises(self, valid_config_dict: dict) -> None:
        """ruff_config pointing to nonexistent file should raise."""
        valid_config_dict["ruff_config"] = "/nonexistent/ruff.toml"
        with pytest.raises(ValueError, match="ruff_config does not exist"):
            DAIOConfig(**valid_config_dict)

    def test_llamacpp_requires_gguf_path(self, valid_config_dict: dict) -> None:
        """llamacpp backend without gguf_model_path should raise."""
        valid_config_dict["backend"] = "llamacpp"
        with pytest.raises(ValueError, match="gguf_model_path must be provided"):
            DAIOConfig(**valid_config_dict)

    def test_invalid_backend_enum_raises(self, valid_config_dict: dict) -> None:
        """Unknown backend should fail enum validation."""
        valid_config_dict["backend"] = "unknown-backend"
        with pytest.raises(Exception):
            DAIOConfig(**valid_config_dict)

    def test_invalid_sast_tool_enum_raises(self, valid_config_dict: dict) -> None:
        """Unknown sast_tool should fail enum validation."""
        valid_config_dict["sast_tool"] = "unknown-tool"
        with pytest.raises(Exception):
            DAIOConfig(**valid_config_dict)

    def test_invalid_type_checker_enum_raises(self, valid_config_dict: dict) -> None:
        """Unknown type_checker should fail enum validation."""
        valid_config_dict["type_checker"] = "unknown-checker"
        with pytest.raises(Exception):
            DAIOConfig(**valid_config_dict)

    def test_invalid_token_counter_backend_enum_raises(self, valid_config_dict: dict) -> None:
        """Unknown token counter backend should fail enum validation."""
        valid_config_dict["token_counter_backend"] = "unknown-backend"
        with pytest.raises(Exception):
            DAIOConfig(**valid_config_dict)

    def test_invalid_n_gpu_layers_string_raises(self, valid_config_dict: dict) -> None:
        """n_gpu_layers should reject unsupported string keywords."""
        valid_config_dict["n_gpu_layers"] = "sometimes"
        with pytest.raises(ValueError, match="n_gpu_layers must be an int, 'auto', or 'all'"):
            DAIOConfig(**valid_config_dict)


# ---------------------------------------------------------------------------
# load_config tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for YAML config file loading."""

    def test_load_valid_config(self, config_yaml: Path) -> None:
        """Load a valid config.yaml and verify model field."""
        config = load_config(config_yaml)
        assert config.model == "test-model:7b-q8_0"

    def test_load_nonexistent_file_raises(self) -> None:
        """Loading a nonexistent config file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config(Path("/nonexistent/config.yaml"))

    def test_load_non_dict_yaml_raises(self, tmp_path: Path) -> None:
        """YAML file containing a list instead of mapping should raise TypeError."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(TypeError, match="YAML mapping"):
            load_config(bad_yaml)
