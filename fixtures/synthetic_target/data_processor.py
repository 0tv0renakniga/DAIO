"""Synthetic target — data processing functions for smoke testing.

Includes edge cases: async def, decorators, nested function, empty function.
"""

import functools
from typing import Any


def flatten(nested_list):
    result = []
    for item in nested_list:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result


def chunk_list(data, size):
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [data[i : i + size] for i in range(0, len(data), size)]


def deduplicate(items):
    seen = set()
    result = []
    for item in items:
        key = id(item) if not isinstance(item, (int, float, str, bool)) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


async def fetch_and_transform(url, transformer):
    # Simulated async function for testing async def handling
    data = {"url": url, "status": "mock"}
    return transformer(data)


def memoize(func):
    @functools.wraps(func)
    def wrapper(*args):
        if args not in wrapper.cache:
            wrapper.cache[args] = func(*args)
        return wrapper.cache[args]

    wrapper.cache = {}
    return wrapper


def noop():
    pass


def process_with_callback(items: list[Any], callback):
    def _inner_process(item):
        # This is a nested function — should be SKIPPED by the Cartographer
        return callback(item)

    return [_inner_process(item) for item in items]
