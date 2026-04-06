# DAIO Refactoring Rules

## Objective
Add Google-style docstrings to every function and method.

## Requirements
1. Every function MUST have a docstring immediately after the `def` line.
2. Use Google-style format with the following sections (when applicable):
   - One-line summary (imperative mood, e.g., "Compute the factorial.")
   - Extended description (only if the function is non-trivial)
   - Args: parameter name, type, and description
   - Returns: type and description
   - Raises: exception type and condition

## Constraints
- Do NOT modify the function logic, signature, or return values.
- Do NOT add, remove, or reorder imports.
- Do NOT rename variables or parameters.
- Preserve ALL existing comments.
- Preserve the exact indentation style of the original code.

## Example

```python
def calculate_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Calculate the Euclidean distance between two 2D points.

    Args:
        x1: X-coordinate of the first point.
        y1: Y-coordinate of the first point.
        x2: X-coordinate of the second point.
        y2: Y-coordinate of the second point.

    Returns:
        The Euclidean distance as a float.
    """
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
```
