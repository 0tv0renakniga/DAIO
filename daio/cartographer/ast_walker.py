"""AST walker — extract function and class definitions from Python source files.

Visits FunctionDef, AsyncFunctionDef, and ClassDef nodes at the module level.
Nested functions are flagged but not processed independently — they travel
with their parent function.

Design decisions:
    - Only top-level functions/methods are extracted as processable units.
    - Class methods are extracted individually (not the whole class).
    - Nested functions (closures) are flagged as nested=True, status=SKIPPED.
    - end_line is determined by the last line of the function body to handle
      trailing decorators and whitespace correctly.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FunctionInfo:
    """Metadata for a single extracted function or method.

    Attributes:
        name: Function name as it appears in source.
        start_line: 1-indexed first line (includes decorators).
        end_line: 1-indexed last line of the function body.
        body_loc: Number of lines in the function body (end - start + 1).
        has_docstring: Whether the function has an existing docstring.
        is_async: Whether this is an async def.
        is_method: Whether this function is defined inside a class.
        class_name: Parent class name, or None for module-level functions.
        decorators: List of decorator names (strings).
        nested: Whether this function is nested inside another function.
    """

    name: str
    start_line: int
    end_line: int
    body_loc: int
    has_docstring: bool
    is_async: bool
    is_method: bool
    class_name: str | None
    decorators: list[str]
    nested: bool = False


@dataclass
class FileAnalysis:
    """Result of analyzing a single Python file.

    Attributes:
        filepath: Absolute path to the analyzed file.
        functions: List of extracted FunctionInfo objects.
        parse_error: Error message if AST parsing failed, else None.
    """

    filepath: Path
    functions: list[FunctionInfo] = field(default_factory=list)
    parse_error: str | None = None


def _get_decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract decorator names from a function node.

    Args:
        node: AST function definition node.

    Returns:
        List of decorator name strings. Complex expressions become '<complex>'.
    """
    names: list[str] = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(f"{_attr_chain(dec)}")
        elif isinstance(dec, ast.Call):
            # e.g., @decorator(args)
            if isinstance(dec.func, ast.Name):
                names.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                names.append(_attr_chain(dec.func))
            else:
                names.append("<complex>")
        else:
            names.append("<complex>")
    return names


def _attr_chain(node: ast.Attribute) -> str:
    """Reconstruct a dotted attribute chain like 'module.submodule.attr'.

    Args:
        node: AST Attribute node.

    Returns:
        Dotted string representation.
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _has_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function node has a docstring.

    Args:
        node: AST function definition node.

    Returns:
        True if the first statement is a string constant (docstring).
    """
    if not node.body:
        return False
    first_stmt = node.body[0]
    return isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Constant) and isinstance(first_stmt.value.value, str)


def _compute_end_line(node: ast.AST, source_lines: list[str]) -> int:
    """Compute the true end line of a function/class node.

    V1.1 Fix #1: Forward-scans past trailing comments and blank lines
    that logically belong to the function body. Without this, dangling
    comments between functions get orphaned after code replacement.

    Args:
        node: The function or class AST node.
        source_lines: Full source as a list of lines (0-indexed).

    Returns:
        1-indexed end line number.
    """
    max_line = node.end_lineno or node.lineno
    total_lines = len(source_lines)

    # Determine the function body's base indentation
    # (the indentation of the `def` line itself)
    def_indent = 0
    if hasattr(node, "lineno") and node.lineno >= 1:
        def_line = source_lines[node.lineno - 1]
        def_indent = len(def_line) - len(def_line.lstrip())

    # The body of a function is indented deeper than the def line.
    # Trailing comments/blanks that belong to this function will be
    # at depth > def_indent OR be completely blank.
    body_indent = def_indent + 1  # any indent strictly greater

    idx = max_line  # 0-indexed: max_line is 1-indexed, so source_lines[max_line] is the next line
    while idx < total_lines:
        line = source_lines[idx]
        stripped = line.strip()

        # Blank lines — consume (could belong to function)
        if not stripped:
            idx += 1
            continue

        # Comment-only lines at body indent or deeper — consume
        line_indent = len(line) - len(line.lstrip())
        if stripped.startswith("#") and line_indent > def_indent:
            idx += 1
            max_line = idx  # extend the end line
            continue

        # Any real code or top-level comment — stop
        break

    return max_line


def _compute_start_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Compute the true start line including decorators.

    Args:
        node: AST function definition node.

    Returns:
        1-indexed start line (first decorator or def line).
    """
    if node.decorator_list:
        return min(dec.lineno for dec in node.decorator_list)
    return node.lineno


def _extract_functions_from_body(
    body: list[ast.stmt],
    source_lines: list[str],
    *,
    class_name: str | None = None,
    parent_is_function: bool = False,
) -> list[FunctionInfo]:
    """Recursively extract function definitions from an AST body.

    Args:
        body: List of AST statement nodes.
        source_lines: Full source as a list of lines.
        class_name: Name of the enclosing class, if any.
        parent_is_function: True if the enclosing scope is a function (nested).

    Returns:
        List of FunctionInfo objects found in this scope.
    """
    results: list[FunctionInfo] = []

    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = _compute_start_line(node)
            end = _compute_end_line(node, source_lines)
            info = FunctionInfo(
                name=node.name,
                start_line=start,
                end_line=end,
                body_loc=end - start + 1,
                has_docstring=_has_docstring(node),
                is_async=isinstance(node, ast.AsyncFunctionDef),
                is_method=class_name is not None,
                class_name=class_name,
                decorators=_get_decorator_names(node),
                nested=parent_is_function,
            )
            results.append(info)

            # Recurse into function body to find nested functions
            # (they'll be flagged as nested=True)
            nested = _extract_functions_from_body(
                node.body,
                source_lines,
                class_name=class_name,
                parent_is_function=True,
            )
            results.extend(nested)

        elif isinstance(node, ast.ClassDef):
            # Extract methods from class body
            methods = _extract_functions_from_body(
                node.body,
                source_lines,
                class_name=node.name,
                parent_is_function=False,
            )
            results.extend(methods)

    return results


def analyze_file(filepath: Path) -> FileAnalysis:
    """Parse a Python file and extract all function/method definitions.

    Args:
        filepath: Path to the Python file to analyze.

    Returns:
        FileAnalysis with extracted functions, or parse_error if parsing failed.

    Raises:
        FileNotFoundError: If the file does not exist (not caught — caller should handle).

    Examples:
        >>> analysis = analyze_file(Path("my_module.py"))
        >>> for func in analysis.functions:
        ...     print(f"{func.name}: lines {func.start_line}-{func.end_line}")
    """
    filepath = filepath.resolve()
    source_text = filepath.read_text(encoding="utf-8")
    source_lines = source_text.splitlines()

    try:
        tree = ast.parse(source_text, filename=str(filepath))
    except SyntaxError as exc:
        return FileAnalysis(
            filepath=filepath,
            parse_error=f"SyntaxError at line {exc.lineno}: {exc.msg}",
        )

    functions = _extract_functions_from_body(
        tree.body,
        source_lines,
        class_name=None,
        parent_is_function=False,
    )

    return FileAnalysis(filepath=filepath, functions=functions)


def collect_files(target_path: Path, scope: str, file_list: list[str] | None = None) -> list[Path]:
    """Collect Python files from the target path based on scope.

    Args:
        target_path: Root path of the target codebase.
        scope: One of 'full', 'module', 'filelist'.
        file_list: Explicit file paths when scope is 'filelist'.

    Returns:
        Sorted list of absolute paths to Python files.

    Raises:
        ValueError: If scope is 'filelist' but file_list is empty.
        FileNotFoundError: If target_path does not exist.
    """
    target_path = target_path.resolve()
    if not target_path.exists():
        msg = f"Target path does not exist: {target_path}"
        raise FileNotFoundError(msg)

    if scope == "filelist":
        if not file_list:
            msg = "file_list must be provided when scope is 'filelist'"
            raise ValueError(msg)
        paths = []
        for f in file_list:
            p = (target_path / f).resolve()
            if p.exists() and p.suffix == ".py":
                paths.append(p)
        return sorted(paths)

    # scope == "full" or "module" — recursively find all .py files
    # Exclude common non-source directories
    exclude_dirs = {
        "__pycache__", ".git", ".venv", "venv", "node_modules",
        ".tox", ".eggs", "*.egg-info", "dist", "build",
    }
    paths: list[Path] = []
    for py_file in sorted(target_path.rglob("*.py")):
        # Check if any parent directory should be excluded
        parts = py_file.relative_to(target_path).parts
        if any(part in exclude_dirs or part.endswith(".egg-info") for part in parts):
            continue
        paths.append(py_file)

    return paths
