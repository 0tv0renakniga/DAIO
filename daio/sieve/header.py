"""Import and constant relevance filter — build a pruned global header.

Parses file-level imports and constants, then filters to only those
actually referenced in the target function snippet. This keeps the
work packet's context window tight.
"""

from __future__ import annotations

import ast
from pathlib import Path


def collect_file_imports(source_text: str) -> list[str]:
    """Extract all top-level import statements from source text.

    Args:
        source_text: Full Python source as a string.

    Returns:
        List of import statement lines (as strings).
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    import_lines: list[str] = []
    source_lines = source_text.splitlines()

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Capture the full import statement (may span multiple lines)
            start = node.lineno - 1  # 0-indexed
            end = node.end_lineno or node.lineno
            stmt_lines = source_lines[start:end]
            import_lines.append("\n".join(stmt_lines))

    return import_lines


def collect_constants(source_text: str) -> list[str]:
    """Extract top-level constant assignments (UPPER_CASE names).

    Only captures simple assignments at module level where the target
    name is ALL_CAPS (conventional constant style).

    Args:
        source_text: Full Python source as a string.

    Returns:
        List of constant assignment lines.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    source_lines = source_text.splitlines()
    constants: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    start = node.lineno - 1
                    end = node.end_lineno or node.lineno
                    stmt_lines = source_lines[start:end]
                    constants.append("\n".join(stmt_lines))

    return constants


def extract_identifiers_from_snippet(snippet_lines: list[str]) -> set[str]:
    """Extract all identifier names used in a code snippet.

    Uses AST parsing to find Name and Attribute nodes. Falls back to
    a simple regex scan if the snippet can't be parsed as a standalone
    module (common for methods that reference `self`).

    Args:
        snippet_lines: Lines of the function code.

    Returns:
        Set of identifier name strings found in the snippet.
    """
    snippet_text = "\n".join(snippet_lines)
    identifiers: set[str] = set()

    try:
        tree = ast.parse(snippet_text)
    except SyntaxError:
        # Fallback: simple word extraction for unparseable snippets
        # (e.g., methods with unresolved self references)
        import re
        words = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", snippet_text)
        return set(words)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
            # Also capture the root of the attribute chain
            current = node.value
            while isinstance(current, ast.Attribute):
                identifiers.add(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                identifiers.add(current.id)

    return identifiers


def _import_provides_name(import_stmt: str, name: str) -> bool:
    """Check if an import statement provides a specific name.

    Handles:
        - `import foo` → provides 'foo'
        - `import foo.bar` → provides 'foo'
        - `from foo import bar` → provides 'bar'
        - `from foo import bar as baz` → provides 'baz'
        - `from foo import *` → always matches (conservative)

    Args:
        import_stmt: The import statement string.
        name: The identifier to check for.

    Returns:
        True if this import could provide the given name.
    """
    try:
        tree = ast.parse(import_stmt)
    except SyntaxError:
        # Conservative: keep the import if we can't parse it
        return True

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                effective = alias.asname or alias.name.split(".")[0]
                if effective == name:
                    return True
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return True  # Star import — always match
                effective = alias.asname or alias.name
                if effective == name:
                    return True

    return False


def filter_imports(
    import_stmts: list[str],
    snippet_identifiers: set[str],
) -> list[str]:
    """Filter imports to only those referenced in the snippet.

    Args:
        import_stmts: All import statements from the file.
        snippet_identifiers: Set of identifiers used in the function snippet.

    Returns:
        Filtered list of import statements that provide at least one
        identifier used in the snippet.
    """
    relevant: list[str] = []
    for stmt in import_stmts:
        for name in snippet_identifiers:
            if _import_provides_name(stmt, name):
                relevant.append(stmt)
                break
    return relevant


def filter_constants(
    constant_stmts: list[str],
    snippet_identifiers: set[str],
) -> list[str]:
    """Filter constants to only those referenced in the snippet.

    Args:
        constant_stmts: All constant assignments from the file.
        snippet_identifiers: Set of identifiers used in the function snippet.

    Returns:
        Filtered list of constant assignments.
    """
    relevant: list[str] = []
    for stmt in constant_stmts:
        try:
            tree = ast.parse(stmt)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in snippet_identifiers:
                        relevant.append(stmt)
                        break
    return relevant


def build_global_header(
    source_text: str,
    snippet_lines: list[str],
    header_token_budget: int = 512,
) -> str:
    """Build a pruned global header for a work packet.

    Collects file imports and constants, filters to only those
    referenced in the snippet, and caps at the token budget.

    Args:
        source_text: Full source file as a string.
        snippet_lines: The extracted function code lines.
        header_token_budget: Maximum token estimate for the header.

    Returns:
        The assembled header string. May include a TRUNCATED comment
        if the full relevant context exceeds the budget.
    """
    identifiers = extract_identifiers_from_snippet(snippet_lines)

    imports = collect_file_imports(source_text)
    constants = collect_constants(source_text)

    relevant_imports = filter_imports(imports, identifiers)
    relevant_constants = filter_constants(constants, identifiers)

    # Assemble header
    parts: list[str] = []
    if relevant_imports:
        parts.extend(relevant_imports)
    if relevant_constants:
        if parts:
            parts.append("")  # blank separator
        parts.extend(relevant_constants)

    header = "\n".join(parts)

    # Token estimate (chars / 4)
    estimated_tokens = len(header) // 4
    if estimated_tokens > header_token_budget:
        # Truncate: keep imports (higher priority), drop constants
        header = "\n".join(relevant_imports)
        estimated_tokens = len(header) // 4

        if estimated_tokens > header_token_budget:
            # Still over budget — truncate imports too
            truncated_imports: list[str] = []
            running_tokens = 0
            omitted = 0
            for imp in relevant_imports:
                imp_tokens = len(imp) // 4
                if running_tokens + imp_tokens <= header_token_budget:
                    truncated_imports.append(imp)
                    running_tokens += imp_tokens
                else:
                    omitted += 1

            truncated_imports.append(
                f"# [TRUNCATED — {omitted} imports omitted due to token budget]"
            )
            header = "\n".join(truncated_imports)
        elif relevant_constants:
            omitted_count = len(relevant_constants)
            header += f"\n# [TRUNCATED — {omitted_count} constants omitted due to token budget]"

    return header
