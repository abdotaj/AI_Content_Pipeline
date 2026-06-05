# ============================================================
#  agents/vimax_bridge.py  —  ViMax Storyboard Bridge
#
#  Generates scene-specific image prompts using ViMax's
#  storyboard approach (cinematic shot planning per script section).
#
#  Provider priority:
#    1. Ollama (local, free, best quality when running)
#    2. Groq  (cloud, existing API key, GitHub Actions fallback)
#    3. []    (silent fallback → existing tiered prompt system takes over)
#
#  No new pip dependencies — uses only `requests` (already installed).
# ============================================================

import json
import os
import re
import time
import requests

_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
_IMAGE_SUFFIX    = ", dark cinematic documentary style, no text, no watermarks, photorealistic, high detail"

# ── ViMax-style storyboard system prompt ─────────────────────────────────────
# Adapted from ViMax's StoryboardArtist + decompose_visual_description agents.
# Produces shot-level image prompts (first-frame description) ready for Pollinations.
_SYSTEM_PROMPT = """\
You are a professional documentary storyboard artist.

Given a script, create {num_shots} cinematic shot descriptions for Pollinations AI \
image generation. Apply ViMax shot-planning principles:

SHOT TYPES (use all):
- Aerial wide shot: cities, landscapes, war zones, borders
- Environmental medium: buildings, markets, prisons, deserts
- Object close-up: documents, weapons, gold, artifacts
- Historical scene: era-specific crowd, protest, battle aftermath

RULES:
- Each prompt must reflect what is actually happening in that part of the script
- Specify real locations, real historical periods (e.g. "Darfur Sudan 2003")
- Vertical 9:16 format, dark dramatic documentary lighting
- NO human faces, NO text/writing in image, NO watermarks
- 20-40 words per prompt, concrete and visual — not abstract
- Output ONLY a JSON array of {num_shots} strings. No explanation, no markdown.

GOOD: "Aerial view Darfur Sudan 2003, burned village ruins smoke rising, desert horizon, \
golden hour dramatic documentary 9:16"
BAD: "dark crime background scene"
"""

_HUMAN_PROMPT = """\
Script ({language}):
{script}

Create {num_shots} Pollinations image prompts for this script. \
JSON array only.\
"""


def _is_ollama_available() -> bool:
    """Return True if Ollama is reachable on localhost."""
    try:
        r = requests.get(f"{_OLLAMA_BASE_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _call_ollama(system: str, user: str, max_tokens: int = 800) -> str:
    """Call Ollama's OpenAI-compatible endpoint. Returns content string."""
    r = requests.post(
        f"{_OLLAMA_BASE_URL}/v1/chat/completions",
        json={
            "model":      _OLLAMA_MODEL,
            "messages":   [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_tokens":  max_tokens,
            "temperature": 0.7,
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _call_groq(system: str, user: str, max_tokens: int = 800) -> str:
    """Call Groq as cloud fallback. Returns content string or raises."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model":       "llama-3.3-70b-versatile",
            "messages":    [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_tokens":  max_tokens,
            "temperature": 0.7,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _parse_prompt_list(raw: str, expected: int) -> list[str]:
    """Extract a JSON array of strings from raw LLM output."""
    # Strip markdown code fences if present
    raw = re.sub(r"```[a-zA-Z]*\n?", "", raw).strip()

    # Find first [ ... ] block
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if m:
        try:
            prompts = json.loads(m.group())
            if isinstance(prompts, list) and prompts:
                return [str(p).strip() for p in prompts if str(p).strip()]
        except json.JSONDecodeError:
            pass

    # Fallback: extract quoted strings
    prompts = re.findall(r'"([^"]{20,})"', raw)
    if prompts:
        return prompts

    return []


def _split_sections(script: str) -> list[str]:
    """Split script by [SECTION:] markers into a list of text blocks."""
    parts = re.split(r'\[SECTION:[^\]]*\]', script, flags=re.IGNORECASE)
    sections = [p.strip() for p in parts if p.strip() and len(p.strip()) > 80]
    return sections if sections else [script]


def generate_storyboard_prompts(
    script: str,
    topic: str = "",
    language: str = "english",
    num_shots: int = 10,
    style: str = "true crime documentary",
) -> list[str]:
    """
    Generate ViMax-style storyboard image prompts for a script.

    Returns a list of [num_shots] Pollinations-ready prompt strings,
    or [] if both Ollama and Groq are unavailable (silent fallback).
    """
    if not script or not script.strip():
        return []

    sections = _split_sections(script)
    shots_per_section = max(2, num_shots // len(sections))
    # Give remaining shots to the main body section
    section_shot_counts = [shots_per_section] * len(sections)
    section_shot_counts[min(1, len(sections) - 1)] += num_shots - sum(section_shot_counts)

    all_prompts: list[str] = []
    provider = "none"

    ollama_ok = _is_ollama_available()
    if ollama_ok:
        provider = "Ollama"
        print(f"[ViMax Bridge] Ollama available ({_OLLAMA_MODEL}) — generating storyboard")
    else:
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            provider = "Groq"
            print("[ViMax Bridge] Ollama offline — using Groq fallback for storyboard")
        else:
            print("[ViMax Bridge] Ollama offline, no Groq key — skipping storyboard")
            return []

    for idx, (section_text, shot_count) in enumerate(zip(sections, section_shot_counts)):
        if shot_count < 1:
            continue

        system = _SYSTEM_PROMPT.format(num_shots=shot_count)
        user   = _HUMAN_PROMPT.format(
            language=language,
            script=section_text[:2000],   # cap to avoid context overflow
            num_shots=shot_count,
        )

        try:
            if provider == "Ollama":
                raw = _call_ollama(system, user, max_tokens=shot_count * 80)
            else:
                raw = _call_groq(system, user, max_tokens=shot_count * 80)

            section_prompts = _parse_prompt_list(raw, shot_count)
            if section_prompts:
                print(f"[ViMax Bridge] Section {idx+1}/{len(sections)}: {len(section_prompts)} shots generated")
                all_prompts.extend(section_prompts[:shot_count])
            else:
                print(f"[ViMax Bridge] Section {idx+1}: parse failed, skipping")

        except Exception as e:
            print(f"[ViMax Bridge] Section {idx+1} failed ({provider}): {e}")

        # Respect Groq rate limits (6000 TPM on free tier)
        if provider == "Groq" and idx < len(sections) - 1:
            time.sleep(3)

    if not all_prompts:
        return []

    # Pad or trim to exact count
    if len(all_prompts) < num_shots:
        all_prompts = (all_prompts * ((num_shots // len(all_prompts)) + 1))[:num_shots]
    else:
        all_prompts = all_prompts[:num_shots]

    # Append Pollinations style suffix to each prompt
    final = [f"{p.rstrip(',')}{_IMAGE_SUFFIX}" for p in all_prompts]
    print(f"[ViMax Bridge] Storyboard complete: {len(final)} prompts via {provider}")
    return final
