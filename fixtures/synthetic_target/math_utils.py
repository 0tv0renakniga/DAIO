"""Synthetic target — math utility functions for smoke testing.

These functions deliberately lack docstrings, type hints, and
consistent style to serve as a refactoring target.
"""

import math


def factorial(n):
    if n < 0:
        raise ValueError("negative input")
    if n <= 1:
        return 1
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def fibonacci(n):
    if n < 0:
        raise ValueError("negative input")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def is_prime(n):
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


def gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
