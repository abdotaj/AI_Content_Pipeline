# agents/ai_cache.py — SHA256-based persistent cache for AI responses
#
# Persists to .cache/ai/<task_type>/<hash>.json across GitHub Actions runs.
# Never raises — corrupt or missing cache files are treated as misses.
#
# Public API:
#   cached_ai_call(prompt, model, task_type, fn, ttl_days=None) -> str
#   cache_get(prompt, model, task_type, ttl_days=None)           -> str | None
#   cache_set(prompt, model, task_type, response)                -> None

import hashlib
import json
import os
import time

_CACHE_DIR = ".cache/ai"

# Default TTL per task type (days)
_DEFAULT_TTL: dict[str, int] = {
    "hook_score":    30,
    "hook_gen":      30,
    "metadata":      30,
    "image_prompt":  14,
    "research":       7,
    "outline":       30,
    "translation":   30,
    "entity":        30,
}


def _cache_key(prompt: str, model: str, task_type: str) -> str:
    raw = f"{task_type}|{model}|{prompt}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]


def _cache_path(key: str, task_type: str) -> str:
    d = os.path.join(_CACHE_DIR, task_type)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, f"{key}.json")


def cache_get(prompt: str, model: str, task_type: str,
              ttl_days: int | None = None) -> str | None:
    """Return cached response string, or None on miss/expiry/error."""
    ttl = ttl_days if ttl_days is not None else _DEFAULT_TTL.get(task_type, 30)
    key  = _cache_key(prompt, model, task_type)
    path = _cache_path(key, task_type)
    try:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        age_days = (time.time() - data.get("ts", 0)) / 86400.0
        if age_days > ttl:
            try:
                os.remove(path)
            except Exception:
                pass
            return None
        response = data.get("response")
        if response:
            print(f"[Cache] HIT {task_type}: {prompt[:50]!r}")
        return response
    except Exception:
        return None


def cache_set(prompt: str, model: str, task_type: str, response: str) -> None:
    """Persist response to cache. Silent on failure."""
    if not response:
        return
    key  = _cache_key(prompt, model, task_type)
    path = _cache_path(key, task_type)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "response":  response,
                "model":     model,
                "task_type": task_type,
                "ts":        time.time(),
            }, f, ensure_ascii=False)
        print(f"[Cache] SAVED {task_type}: {prompt[:50]!r}")
    except Exception as e:
        print(f"[Cache] Save failed ({task_type}): {e}")


def cached_ai_call(prompt: str, model: str, task_type: str,
                   fn, ttl_days: int | None = None) -> str:
    """
    Cache-aware wrapper around any AI call.

    fn must be a zero-argument callable that returns a response string.
    If FORCE_REFRESH=1 is set in the environment, always bypasses the cache
    (but still saves the fresh result).

    Usage:
        result = cached_ai_call(
            prompt, "gpt-4o-mini", "hook_score",
            fn=lambda: _score_hook_raw(hook),
            ttl_days=30,
        )
    """
    force = os.getenv("FORCE_REFRESH", "").strip() == "1"

    if not force:
        cached = cache_get(prompt, model, task_type, ttl_days)
        if cached is not None:
            return cached

    print(f"[Cache] MISS {task_type}: {prompt[:50]!r}")
    try:
        result = fn()
    except Exception as e:
        print(f"[Cache] fn() raised for {task_type}: {e}")
        result = ""

    if result:
        cache_set(prompt, model, task_type, result)

    return result or ""
