# agents/json_utils.py — Safe JSON parsing helpers for the pipeline
#
# Every AI provider can return HTML error pages, plain text, empty strings,
# or JSON wrapped in markdown fences. This module centralises all that
# handling so individual callers never crash on a bad response.
#
# Public API:
#   safe_json_parse(text, fallback=None) -> dict | list | any
#   is_valid_json_response(text)         -> bool
#   strip_markdown_fences(text)          -> str

import json
import re

# Response prefixes that are definitely NOT JSON (HTML errors, rate-limit pages, etc.)
_INVALID_PREFIXES = (
    "<!DOCTYPE", "<html", "<HTML", "<head",
    "Rate limit", "Rate Limit", "RateLimit",
    "Too Many Requests",
    "Error:", "error:",
    "Access denied", "Forbidden",
    "Internal Server Error",
)

# Valid first characters for JSON values
_VALID_JSON_STARTS = frozenset(
    ('{', '[', '"', 't', 'f', 'n',
     '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '-')
)


def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers that some models add."""
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def is_valid_json_response(text: str) -> bool:
    """Return True only if *text* looks like it could be valid JSON."""
    if not text:
        return False
    t = text.strip()
    if not t:
        return False
    for prefix in _INVALID_PREFIXES:
        if t.startswith(prefix):
            return False
    return t[0] in _VALID_JSON_STARTS


def safe_json_parse(text, fallback=None):
    """
    Parse *text* as JSON, returning *fallback* on any failure.

    Handles:
    - None / empty string input
    - Markdown code fences (```json ... ```)
    - HTML error pages or rate-limit messages
    - Malformed / truncated JSON

    *fallback* defaults to {} when not provided so callers can always call .get().
    """
    if fallback is None:
        fallback = {}

    if not text:
        print("[JSON] Empty response — using fallback")
        return fallback

    text = strip_markdown_fences(str(text))

    if not text:
        print("[JSON] Empty after stripping fences — using fallback")
        return fallback

    if not is_valid_json_response(text):
        print(f"[JSON] Non-JSON response skipped: {text[:80]!r}")
        return fallback

    try:
        return json.loads(text)
    except Exception as e:
        print(f"[JSON] Parse failed ({e}) — using fallback")
        print(f"[JSON] Raw (first 200 chars): {text[:200]!r}")
        return fallback
