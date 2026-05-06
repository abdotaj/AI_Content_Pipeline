# agents/json_utils.py — Safe JSON parsing helpers for the pipeline
#
# Every AI provider can return HTML error pages, plain text, empty strings,
# or JSON wrapped in markdown fences. This module centralises all that
# handling so individual callers never crash on a bad response.
#
# Public API:
#   safe_json_parse(text, fallback=None)                         -> dict | list | any
#   normalize_ai_json_response(text, required_keys, list_keys)   -> dict
#   is_valid_json_response(text)                                 -> bool
#   strip_markdown_fences(text)                                  -> str

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


def normalize_ai_json_response(text, required_keys=None, list_keys=None):
    """
    Full normalization layer for structured AI responses.

    1. Parse safely via safe_json_parse (handles None, HTML, fences, bad JSON).
    2. Ensure result is a dict — if AI returned a list or primitive, reset to {}.
    3. Guarantee every key in required_keys exists (sets missing ones to None).
    4. Coerce any field in list_keys from string/None to list:
       - string  -> [string]   (OpenAI sometimes collapses arrays during fallback)
       - None    -> []
       - already a list -> unchanged

    Args:
        text:          Raw AI response string.
        required_keys: Iterable of key names that must exist in the result.
        list_keys:     Iterable of key names whose values must be lists.

    Returns:
        A dict, always. Never raises.
    """
    data = safe_json_parse(text, fallback={})

    if not isinstance(data, dict):
        print(f"[JSON] normalize: top-level value is {type(data).__name__}, not dict — resetting")
        data = {}

    if required_keys:
        for key in required_keys:
            data.setdefault(key, None)

    if list_keys:
        for key in list_keys:
            val = data.get(key)
            if val is None:
                data[key] = []
            elif isinstance(val, str):
                # OpenAI collapses single-element arrays to strings in some fallback modes
                print(f"[JSON] normalize: '{key}' was a string — wrapping in list")
                data[key] = [val]
            elif not isinstance(val, list):
                # Any other scalar (int, bool, etc.) — wrap it
                data[key] = [val]

    return data
