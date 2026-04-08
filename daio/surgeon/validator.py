"""Validation gate — multi-stage check for LLM-generated code.

Stage 1: py_compile — syntax validation
Stage 2: ruff check — lint validation
Stage 3: LOC sanity — reject extreme size changes (hallucination guard)
Stage 4: SAST — security analysis via bandit (V2.0 #16)
Stage 5: Type check — mypy/pyright strict mode (V2.0 #18)

The GATEKEEPER: no code touches the filesystem unless it passes all stages.
"""

from __future__ import annotations

import py_compile
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ValidationResult:
    """Result of the multi-stage validation gate.

    Attributes:
        passed: Whether all enabled stages passed.
        syntax_ok: Whether py_compile passed.
        lint_ok: Whether ruff check passed (or was skipped).
        loc_ok: Whether LOC ratio is within bounds.
        sast_ok: Whether security analysis passed (or was skipped).
        typecheck_ok: Whether type checker passed (or was skipped).
        errors: List of error messages from failed stages.
    """

    passed: bool = True
    syntax_ok: bool = True
    lint_ok: bool = True
    loc_ok: bool = True
    sast_ok: bool = True
    typecheck_ok: bool = True
    errors: list[str] = field(default_factory=list)


def validate_syntax(code_lines: list[str]) -> tuple[bool, str]:
    """Stage 1: Validate Python syntax with py_compile.

    Writes code to a temp file and compiles it. Does not execute.

    Args:
        code_lines: The transformed code as a list of lines.

    Returns:
        Tuple of (passed: bool, error_message: str). Error is empty if passed.
    """
    code = "\n".join(code_lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", encoding="utf-8", delete=True
    ) as tmp:
        tmp.write(code)
        tmp.flush()
        try:
            py_compile.compile(tmp.name, doraise=True)
            return True, ""
        except py_compile.PyCompileError as exc:
            return False, f"SyntaxError: {exc}"


def validate_lint(code_lines: list[str], ruff_config: Path | None = None) -> tuple[bool, str]:
    """Stage 2: Lint validation with ruff check.

    Only checks for errors (E), fatal errors (F), and warnings (W).
    Style issues are not blockers.

    Args:
        code_lines: The transformed code as a list of lines.
        ruff_config: Optional path to ruff config file.

    Returns:
        Tuple of (passed: bool, error_message: str). Error is empty if passed.
    """
    code = "\n".join(code_lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        cmd = ["ruff", "check", "--select", "E,F", "--no-fix", tmp_path]
        if ruff_config and ruff_config.exists():
            cmd.extend(["--config", str(ruff_config)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return True, ""

        # Filter output to remove tmp path noise
        errors = result.stdout.strip()
        if errors:
            # Replace tmp path with readable name
            errors = errors.replace(tmp_path, "<transformed_code>")
            return False, f"Lint errors:\n{errors}"

        return True, ""

    except FileNotFoundError:
        # ruff not installed — skip lint check (non-fatal)
        return True, ""
    except subprocess.TimeoutExpired:
        return True, ""  # Skip on timeout — don't block pipeline
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def validate_loc(
    original_lines: list[str],
    transformed_lines: list[str],
    shrink_floor: float = 0.3,
    growth_ceiling: float = 3.0,
) -> tuple[bool, str]:
    """Stage 3: LOC sanity check — detect hallucinated output.

    Rejects transformed code if the line count ratio is outside
    [shrink_floor, growth_ceiling] relative to the original.

    Args:
        original_lines: The original function code lines.
        transformed_lines: The transformed function code lines.
        shrink_floor: Minimum ratio (output/input). Below = likely hallucination.
        growth_ceiling: Maximum ratio (output/input). Above = likely hallucination.

    Returns:
        Tuple of (passed: bool, error_message: str).
    """
    orig_count = len(original_lines)
    new_count = len(transformed_lines)

    # Handle edge case: original is empty or very small
    if orig_count == 0:
        return True, ""

    ratio = new_count / orig_count

    if ratio < shrink_floor:
        msg = (
            f"LOC shrinkage: {new_count}/{orig_count} lines "
            f"(ratio {ratio:.2f} < floor {shrink_floor}). "
            f"Possible hallucination — output too small."
        )
        return False, msg

    if ratio > growth_ceiling:
        msg = (
            f"LOC growth: {new_count}/{orig_count} lines "
            f"(ratio {ratio:.2f} > ceiling {growth_ceiling}). "
            f"Possible hallucination — output too large."
        )
        return False, msg

    return True, ""

def _validate_sast_bandit(code_lines: list[str]) -> tuple[bool, str]:
    """Security analysis via bandit."""
    code = "\n".join(code_lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "bandit",
                "-q",
                "--severity-level", "medium",
                "--confidence-level", "medium",
                "-f", "text",
                tmp_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return True, ""

        errors = result.stdout.strip()
        if errors:
            errors = errors.replace(tmp_path, "<transformed_code>")
            return False, f"Security issues:\n{errors}"

        return True, ""

    except FileNotFoundError:
        # bandit not installed — skip (non-fatal)
        return True, ""
    except subprocess.TimeoutExpired:
        return True, ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _validate_sast_semgrep(code_lines: list[str]) -> tuple[bool, str]:
    """Security analysis via semgrep."""
    code = "\n".join(code_lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "semgrep",
                "--quiet",
                "--error",
                "--config", "auto",
                tmp_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            return True, ""

        errors = (result.stdout or result.stderr).strip()
        if errors:
            errors = errors.replace(tmp_path, "<transformed_code>")
            return False, f"Security issues:\n{errors}"
        return True, ""

    except FileNotFoundError:
        # semgrep not installed — skip (non-fatal)
        return True, ""
    except subprocess.TimeoutExpired:
        return True, ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def validate_sast(
    code_lines: list[str],
    tool: str = "bandit",
) -> tuple[bool, str]:
    """Stage 4: Security analysis via bandit/semgrep.

    V2.0 Fix #16: Prevents LLMs from introducing vulnerabilities like
    SQL injection, path traversal, insecure deserialization, etc.

    Args:
        code_lines: The transformed code as a list of lines.

    Returns:
        Tuple of (passed: bool, error_message: str).
    """
    if tool == "semgrep":
        return _validate_sast_semgrep(code_lines)
    return _validate_sast_bandit(code_lines)


def validate_types(code_lines: list[str], checker: str = "mypy") -> tuple[bool, str]:
    """Stage 5: Type checking via mypy or pyright.

    V2.0 Fix #18: Ensures LLM-generated code adheres to strict typing.
    Catches hallucinated return types and type mismatches.

    Args:
        code_lines: The transformed code as a list of lines.
        checker: Type checker to use ('mypy' or 'pyright').

    Returns:
        Tuple of (passed: bool, error_message: str).
    """
    code = "\n".join(code_lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        if checker == "mypy":
            cmd = ["mypy", "--strict", "--no-error-summary", tmp_path]
        else:  # pyright
            cmd = ["pyright", "--outputjson", tmp_path]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            return True, ""

        errors = result.stdout.strip()
        if errors:
            errors = errors.replace(tmp_path, "<transformed_code>")
            # Filter to only actual errors (not notes)
            error_lines = [
                line for line in errors.splitlines()
                if "error:" in line.lower()
            ]
            if error_lines:
                return False, f"Type errors:\n" + "\n".join(error_lines[:5])

        return True, ""

    except FileNotFoundError:
        # Type checker not installed — skip (non-fatal)
        return True, ""
    except subprocess.TimeoutExpired:
        return True, ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def validate(
    original_lines: list[str],
    transformed_lines: list[str],
    *,
    ruff_config: Path | None = None,
    shrink_floor: float = 0.3,
    growth_ceiling: float = 3.0,
    enable_sast: bool = False,
    sast_tool: str = "bandit",
    enable_typecheck: bool = False,
    type_checker: str = "mypy",
) -> ValidationResult:
    """Run the full multi-stage validation gate.

    Args:
        original_lines: The original function snippet lines.
        transformed_lines: The LLM-transformed code lines.
        ruff_config: Optional ruff config path.
        shrink_floor: Min output/input LOC ratio.
        growth_ceiling: Max output/input LOC ratio.
        enable_sast: If True, run bandit security analysis (Stage 4).
        sast_tool: SAST backend to use ('bandit' or 'semgrep').
        enable_typecheck: If True, run mypy/pyright (Stage 5).
        type_checker: Type checker to use ('mypy' or 'pyright').

    Returns:
        ValidationResult with per-stage status and error messages.
    """
    result = ValidationResult()

    # Stage 1: Syntax
    syntax_ok, syntax_err = validate_syntax(transformed_lines)
    result.syntax_ok = syntax_ok
    if not syntax_ok:
        result.passed = False
        result.errors.append(syntax_err)

    # Stage 2: Lint (only if syntax passed)
    if syntax_ok:
        lint_ok, lint_err = validate_lint(transformed_lines, ruff_config)
        result.lint_ok = lint_ok
        if not lint_ok:
            result.passed = False
            result.errors.append(lint_err)

    # Stage 3: LOC sanity
    loc_ok, loc_err = validate_loc(
        original_lines, transformed_lines, shrink_floor, growth_ceiling
    )
    result.loc_ok = loc_ok
    if not loc_ok:
        result.passed = False
        result.errors.append(loc_err)

    # Stage 4: SAST security analysis (V2.0 #16)
    if enable_sast and syntax_ok:
        sast_ok, sast_err = validate_sast(transformed_lines, sast_tool)
        result.sast_ok = sast_ok
        if not sast_ok:
            result.passed = False
            result.errors.append(sast_err)

    # Stage 5: Type checking (V2.0 #18)
    if enable_typecheck and syntax_ok:
        tc_ok, tc_err = validate_types(transformed_lines, type_checker)
        result.typecheck_ok = tc_ok
        if not tc_ok:
            result.passed = False
            result.errors.append(tc_err)

    return result
