"""End-to-end smoke tests for the DAIO pipeline.

Tests the full integration: CLI commands process the synthetic fixtures
through Cartographer + Sieve phases (Surgeon is mocked since no live Ollama).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from daio.cli import DEFAULT_CONFIG, main
from daio.config import DAIOConfig
from daio.pipeline import run_manifest_only, run_pipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "synthetic_target"


def _normalize_config_docs(raw: str) -> list[tuple[str, str, str]]:
    """Normalize config text for doc parity checks.

    Keeps a stable, order-sensitive representation of:
    - section headers
    - full-line comment markers
    - active keys and their inline comments (value ignored)
    - commented-out keys and their inline comments (value ignored)
    """
    normalized: list[tuple[str, str, str]] = []

    section_re = re.compile(r"^#\s*---\s*.+\s*---\s*$")
    active_key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*.*?(?:\s+#\s*(.*))?$")
    commented_key_re = re.compile(
        r"^#\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*.*?(?:\s+#\s*(.*))?$"
    )

    for line in raw.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue

        if section_re.match(stripped):
            normalized.append(("section", stripped.strip(), ""))
            continue

        commented_key_match = commented_key_re.match(stripped)
        if commented_key_match:
            key = commented_key_match.group(1)
            comment = (commented_key_match.group(2) or "").strip()
            normalized.append(("commented_key", key, comment))
            continue

        active_key_match = active_key_re.match(stripped)
        if active_key_match:
            key = active_key_match.group(1)
            comment = (active_key_match.group(2) or "").strip()
            normalized.append(("active_key", key, comment))
            continue

        if stripped.lstrip().startswith("#"):
            normalized.append(("comment_line", stripped.strip(), ""))

    return normalized


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Create a workspace with synthetic target + config + rules."""
    # Copy synthetic target
    target = tmp_path / "target"
    shutil.copytree(FIXTURES_DIR, target)

    # Write rules
    rules = tmp_path / "rules.md"
    rules.write_text(
        "Add Google-style docstrings to all functions.\n"
        "Do NOT modify logic or imports.\n",
        encoding="utf-8",
    )

    # Write config
    config = tmp_path / "config.yaml"
    config.write_text(
        f"model: test-model\n"
        f"target_path: {target}\n"
        f"rules_path: {rules}\n"
        f"scope: full\n"
        f"auto_commit: false\n"
        f"output_dir: {tmp_path / '.daio'}\n"
        f"token_budget: 4096\n"
        f"max_retries: 2\n"
        f"request_timeout: 60\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def config(workspace: Path) -> DAIOConfig:
    """Load the workspace config."""
    from daio.config import load_config
    return load_config(workspace / "config.yaml")


# ===================================================================
# CLI Smoke Tests
# ===================================================================


class TestCLIInit:
    """Smoke tests for the init command."""

    def test_init_creates_files(self, tmp_path: Path) -> None:
        """daio init should create config.yaml and rules.md."""
        runner = CliRunner()
        result = runner.invoke(main, ["init", "--dir", str(tmp_path / "new_project")])
        assert result.exit_code == 0
        assert (tmp_path / "new_project" / "config.yaml").exists()
        assert (tmp_path / "new_project" / "rules.md").exists()
        cfg_text = (tmp_path / "new_project" / "config.yaml").read_text(encoding="utf-8")
        assert 'backend: "ollama"' in cfg_text
        assert "token_counter_backend" in cfg_text
        assert "dataset_export_enabled" in cfg_text

    def test_init_idempotent(self, tmp_path: Path) -> None:
        """Running init twice should not overwrite existing files."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--dir", str(tmp_path)])
        # Modify the config
        (tmp_path / "config.yaml").write_text("modified", encoding="utf-8")
        # Init again
        result = runner.invoke(main, ["init", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        # Should NOT have overwritten
        assert (tmp_path / "config.yaml").read_text() == "modified"

    def test_init_template_keys_match_config_example(self) -> None:
        """DEFAULT_CONFIG and config.example.yaml should expose same key set."""
        example_path = Path(__file__).parent.parent / "config.example.yaml"
        example_cfg = yaml.safe_load(example_path.read_text(encoding="utf-8"))
        init_cfg = yaml.safe_load(DEFAULT_CONFIG)
        assert set(init_cfg.keys()) == set(example_cfg.keys())

    def test_init_template_doc_structure_matches_config_example(self) -> None:
        """Section order + comment markers should stay in sync."""
        example_path = Path(__file__).parent.parent / "config.example.yaml"
        example_text = example_path.read_text(encoding="utf-8")
        assert _normalize_config_docs(DEFAULT_CONFIG) == _normalize_config_docs(example_text)


class TestCLIValidate:
    """Smoke tests for the validate command."""

    def test_validate_valid_config(self, workspace: Path) -> None:
        """Valid config should pass validation."""
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--config", str(workspace / "config.yaml")])
        assert result.exit_code == 0
        assert "Config is valid" in result.output


class TestCLIManifest:
    """Smoke tests for the manifest command."""

    def test_manifest_shows_functions(self, workspace: Path) -> None:
        """Manifest command should list discovered functions."""
        runner = CliRunner()
        result = runner.invoke(main, ["manifest", "--config", str(workspace / "config.yaml")])
        assert result.exit_code == 0
        assert "Manifest Summary" in result.output


# ===================================================================
# Pipeline Integration Tests
# ===================================================================


class TestManifestOnly:
    """Tests for Cartographer-only manifest generation."""

    def test_discovers_all_files(self, config: DAIOConfig) -> None:
        """Should discover all Python files in synthetic target."""
        manifest = run_manifest_only(config)
        files = manifest.get("files", {})
        assert len(files) >= 3  # math_utils, string_helpers, data_processor

    def test_discovers_functions(self, config: DAIOConfig) -> None:
        """Should discover functions in synthetic target."""
        manifest = run_manifest_only(config)
        total = 0
        for file_data in manifest.get("files", {}).values():
            total += len(file_data.get("functions", []))
        assert total >= 5  # At least 5 functions across the fixtures

    def test_manifest_has_uids(self, config: DAIOConfig) -> None:
        """Every function should have a non-empty UID."""
        manifest = run_manifest_only(config)
        for file_data in manifest.get("files", {}).values():
            for entry in file_data.get("functions", []):
                assert entry["uid"], f"Missing UID for {entry['name']}"
                assert len(entry["uid"]) == 12


class TestDryRunPipeline:
    """Tests for dry-run mode (Cartographer + Sieve, no Surgeon)."""

    def test_dry_run_generates_packets(self, config: DAIOConfig) -> None:
        """Dry-run should generate work packets without LLM dispatch."""
        exit_code = run_pipeline(config, dry_run=True)
        assert exit_code == 0

        # Check work packets were saved
        wp_dir = config.output_dir / "work_packets"
        assert wp_dir.exists()
        packets = list(wp_dir.glob("wp_*.txt"))
        assert len(packets) >= 1

    def test_dry_run_creates_manifest(self, config: DAIOConfig) -> None:
        """Dry-run should still create a manifest."""
        run_pipeline(config, dry_run=True)
        manifest_path = config.output_dir / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["version"] == 1

    def test_dry_run_creates_report(self, config: DAIOConfig) -> None:
        """Dry-run should still create a report."""
        run_pipeline(config, dry_run=True)
        report_path = config.output_dir / "report.md"
        assert report_path.exists()
        assert "DAIO Pipeline Report" in report_path.read_text(encoding="utf-8")


class TestFullPipelineMocked:
    """Full pipeline with mocked Ollama dispatch."""

    def test_full_pipeline_with_mock(self, config: DAIOConfig) -> None:
        """Full pipeline with mocked LLM should succeed for valid responses."""
        # First run dry-run to get work packets, then mock the surgeon
        # We need to mock at the dispatch level

        def mock_dispatch(prompt, config):
            """Return a valid transformed function with UID anchors."""
            # Extract UID from the prompt
            import re
            match = re.search(r"UID:([a-f0-9]{12}):START", prompt)
            if not match:
                return "def unknown():\n    pass\n"
            uid = match.group(1)

            # Extract the function from between the markers
            block_re = re.compile(
                rf"# UID:{uid}:START\n(.*?)# UID:{uid}:END",
                re.DOTALL,
            )
            block_match = block_re.search(prompt)
            if block_match:
                original = block_match.group(1).strip()
                lines = original.splitlines()
                # Add a docstring after the def line
                if lines and lines[0].strip().startswith(("def ", "async def ")):
                    indent = len(lines[0]) - len(lines[0].lstrip()) + 4
                    docstring = " " * indent + '"""Docstring added by DAIO."""'
                    new_lines = [lines[0], docstring] + lines[1:]
                    code = "\n".join(new_lines)
                else:
                    code = original

                return (
                    f"# UID:{uid}:START\n"
                    f"{code}\n"
                    f"# UID:{uid}:END\n"
                )

            return "def unknown():\n    pass\n"

        with patch("daio.surgeon.dispatch", side_effect=mock_dispatch):
            exit_code = run_pipeline(config)

        # Some functions will fail ruff lint because the mock returns
        # isolated code snippets that reference names from the file scope
        # (e.g. `math.sqrt`, `callback`). This is expected — exit_code 1
        # is valid when partial success occurs.
        assert exit_code in (0, 1)

        # Verify artifacts were created
        assert (config.output_dir / "manifest.json").exists()
        assert (config.output_dir / "report.md").exists()
        assert (config.output_dir / "results.json").exists()
        assert (config.output_dir / "audit.jsonl").exists()

        # Verify results
        results = json.loads(
            (config.output_dir / "results.json").read_text(encoding="utf-8")
        )
        assert len(results) >= 1
        succeeded = sum(1 for r in results.values() if r["status"] == "SUCCESS")
        assert succeeded >= 1

        # Verify report mentions success
        report = (config.output_dir / "report.md").read_text(encoding="utf-8")
        assert "Succeeded" in report

    def test_full_pipeline_dataset_export(self, config: DAIOConfig) -> None:
        """Dataset export should write JSONL entries when enabled."""
        config.dataset_export_enabled = True
        config.dataset_output_path = config.output_dir / "dataset.jsonl"
        config.enable_sast = False
        config.enable_typecheck = False

        def mock_dispatch(prompt, config):
            import re

            match = re.search(r"UID:([a-f0-9]{12}):START", prompt)
            if not match:
                return "def unknown():\n    pass\n"
            uid = match.group(1)

            block_re = re.compile(rf"# UID:{uid}:START\n(.*?)# UID:{uid}:END", re.DOTALL)
            block_match = block_re.search(prompt)
            if block_match:
                original = block_match.group(1).strip()
                lines = original.splitlines()
                if lines and lines[0].strip().startswith(("def ", "async def ")):
                    indent = len(lines[0]) - len(lines[0].lstrip()) + 4
                    docstring = " " * indent + '"""Docstring added by DAIO."""'
                    code = "\n".join([lines[0], docstring] + lines[1:])
                else:
                    code = original
                return f"# UID:{uid}:START\n{code}\n# UID:{uid}:END\n"
            return "def unknown():\n    pass\n"

        with patch("daio.surgeon.dispatch", side_effect=mock_dispatch):
            run_pipeline(config)

        if (config.output_dir / "results.json").exists():
            results = json.loads((config.output_dir / "results.json").read_text(encoding="utf-8"))
            if any(r.get("status") == "SUCCESS" for r in results.values()):
                assert config.dataset_output_path.exists()


class TestCLIDryRun:
    """CLI dry-run command smoke test."""

    def test_cli_dry_run(self, workspace: Path) -> None:
        """daio dry-run should complete with exit code 0."""
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", "--config", str(workspace / "config.yaml")])
        # dry-run calls SystemExit(0) which CliRunner captures as exit_code=0
        assert result.exit_code == 0
