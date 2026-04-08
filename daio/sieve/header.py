"""Import, constant, and context relevance filter — build a pruned global header.

Parses file-level imports, constants, type aliases, local function signatures,
and class __init__ bodies, then filters to only those referenced in the
target function snippet. This keeps the work packet's context window tight.

V1.2 additions:
    - #4: Intra-file dependency injection (local function stubs)
    - #8: Type alias and annotated assignment collection
    - #10: Class __init__ context for method refactoring
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


def collect_type_aliases(source_text: str) -> list[str]:
    """Extract top-level type aliases and annotated assignments.

    V1.2 Fix #8: Captures patterns that collect_constants misses:
        - `UserID: TypeAlias = int` (ast.AnnAssign)
        - `type UserDict = dict[str, User]` (ast.TypeAlias, Python 3.12+)
        - Lowercase annotated module-level assignments used as types

    Args:
        source_text: Full Python source as a string.

    Returns:
        List of type alias/annotated assignment lines.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    source_lines = source_text.splitlines()
    aliases: list[str] = []

    for node in ast.iter_child_nodes(tree):
        # ast.AnnAssign: `x: int = 5` or `UserID: TypeAlias = int`
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            stmt_lines = source_lines[start:end]
            aliases.append("\n".join(stmt_lines))

        # ast.TypeAlias: `type UserDict = dict[str, User]` (Python 3.12+)
        if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            stmt_lines = source_lines[start:end]
            aliases.append("\n".join(stmt_lines))

    return aliases


def collect_local_function_stubs(
    source_text: str,
    exclude_name: str,
) -> dict[str, str]:
    """Extract function signature stubs for all module-level functions.

    V1.2 Fix #4: When target_func calls a local helper like _validate_input(),
    the LLM needs to see that function's signature to avoid hallucinating args.

    Returns stubs of the form:
        def _validate_input(data: list[int], strict: bool = False) -> bool: ...

    Args:
        source_text: Full Python source as a string.
        exclude_name: Name of the target function to exclude from stubs.

    Returns:
        Dict mapping function name to its signature stub string.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return {}

    source_lines = source_text.splitlines()
    stubs: dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == exclude_name:
                continue
            # Extract just the def line(s) up to the colon
            def_line = source_lines[node.lineno - 1]
            # If def spans multiple lines, grab them all
            end_of_sig = node.lineno - 1
            for i in range(node.lineno - 1, min(node.lineno + 10, len(source_lines))):
                if ":" in source_lines[i] and not source_lines[i].strip().startswith("#"):
                    end_of_sig = i
                    break
            sig_lines = source_lines[node.lineno - 1 : end_of_sig + 1]
            stub = "\n".join(sig_lines).rstrip()
            if not stub.endswith("..."):
                stub += " ..."
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            stubs[node.name] = stub

        elif isinstance(node, ast.ClassDef):
            # Also extract method stubs from classes
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name == exclude_name:
                        continue
                    qualified = f"{node.name}.{child.name}"
                    sig_line = source_lines[child.lineno - 1]
                    end_of_sig = child.lineno - 1
                    for i in range(child.lineno - 1, min(child.lineno + 10, len(source_lines))):
                        if ":" in source_lines[i] and not source_lines[i].strip().startswith("#"):
                            end_of_sig = i
                            break
                    sig_lines = source_lines[child.lineno - 1 : end_of_sig + 1]
                    stub = "\n".join(sig_lines).rstrip()
                    if not stub.endswith("..."):
                        stub += " ..."
                    stubs[child.name] = stub

    return stubs


def collect_init_body(
    source_text: str,
    class_name: str,
) -> str | None:
    """Extract the __init__ method body for a given class.

    V1.2 Fix #10: When refactoring a class method, the LLM needs to know
    what instance variables exist on `self`. This extracts the __init__
    body so the Sieve can inject it into GLOBAL CONTEXT.

    Args:
        source_text: Full Python source as a string.
        class_name: Name of the parent class.

    Returns:
        The __init__ method code as a string, or None if not found.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return None

    source_lines = source_text.splitlines()

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "__init__":
                    start = child.lineno - 1
                    end = child.end_lineno or child.lineno
                    init_lines = source_lines[start:end]
                    return "\n".join(init_lines)

    return None


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
    *,
    target_function_name: str = "",
    class_name: str | None = None,
) -> str:
    """Build a pruned global header for a work packet.

    Collects file imports, constants, type aliases, local function stubs,
    and class __init__ context, then filters to only those referenced
    in the snippet. Caps at the token budget.

    V1.2 additions:
        - #4: Intra-file function stubs for locally-called helpers
        - #8: Type alias and annotated assignment collection
        - #10: Class __init__ body injection for method context

    Args:
        source_text: Full source file as a string.
        snippet_lines: The extracted function code lines.
        header_token_budget: Maximum token estimate for the header.
        target_function_name: Name of the target function (to exclude from stubs).
        class_name: Parent class name if the target is a method.

    Returns:
        The assembled header string. May include a TRUNCATED comment
        if the full relevant context exceeds the budget.
    """
    identifiers = extract_identifiers_from_snippet(snippet_lines)

    imports = collect_file_imports(source_text)
    constants = collect_constants(source_text)
    type_aliases = collect_type_aliases(source_text)

    relevant_imports = filter_imports(imports, identifiers)
    relevant_constants = filter_constants(constants, identifiers)

    # Fix #8: Filter type aliases by reference
    relevant_aliases: list[str] = []
    for alias in type_aliases:
        try:
            tree = ast.parse(alias)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in identifiers:
                    relevant_aliases.append(alias)
                    break
            if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
                if hasattr(node, "name") and hasattr(node.name, "id"):
                    if node.name.id in identifiers:
                        relevant_aliases.append(alias)
                        break

    # Fix #4: Collect local function stubs for referenced helpers
    local_stubs: list[str] = []
    if target_function_name:
        all_stubs = collect_local_function_stubs(source_text, target_function_name)
        for func_name, stub in all_stubs.items():
            if func_name in identifiers:
                local_stubs.append(stub)

    # Fix #10: Inject __init__ context for class methods
    init_context: str | None = None
    if class_name:
        init_context = collect_init_body(source_text, class_name)

    # Assemble header (priority order: imports > stubs > init > constants > aliases)
    parts: list[str] = []
    if relevant_imports:
        parts.extend(relevant_imports)
    if relevant_aliases:
        if parts:
            parts.append("")  # separator
        parts.extend(relevant_aliases)
    if local_stubs:
        if parts:
            parts.append("")  # separator
        parts.append("# --- Local function signatures ---")
        parts.extend(local_stubs)
    if init_context:
        if parts:
            parts.append("")  # separator
        parts.append("# --- Class __init__ context ---")
        parts.append(init_context)
    if relevant_constants:
        if parts:
            parts.append("")  # separator
        parts.extend(relevant_constants)

    header = "\n".join(parts)

    # Token estimate (chars / 4)
    estimated_tokens = len(header) // 4
    if estimated_tokens > header_token_budget:
        # Truncate: keep imports + stubs (higher priority), drop constants/aliases/init
        priority_parts: list[str] = []
        if relevant_imports:
            priority_parts.extend(relevant_imports)
        if local_stubs:
            if priority_parts:
                priority_parts.append("")
            priority_parts.append("# --- Local function signatures ---")
            priority_parts.extend(local_stubs)

        header = "\n".join(priority_parts)
        estimated_tokens = len(header) // 4

        if estimated_tokens > header_token_budget:
            # Still over budget — truncate imports
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
        else:
            dropped: list[str] = []
            if relevant_constants:
                dropped.append(f"{len(relevant_constants)} constants")
            if relevant_aliases:
                dropped.append(f"{len(relevant_aliases)} type aliases")
            if init_context:
                dropped.append("__init__ context")
            if dropped:
                header += f"\n# [TRUNCATED — {', '.join(dropped)} omitted due to token budget]"

    return header
