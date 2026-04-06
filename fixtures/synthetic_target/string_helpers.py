"""Synthetic target — string helper functions for smoke testing."""


def reverse_string(s):
    return s[::-1]


def is_palindrome(s):
    cleaned = "".join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]


def count_vowels(s):
    return sum(1 for c in s.lower() if c in "aeiou")


def title_case(s):
    return " ".join(word.capitalize() for word in s.split())


def truncate(s, max_length, suffix="..."):
    if len(s) <= max_length:
        return s
    return s[: max_length - len(suffix)] + suffix
