# agents/entity_guard.py — Entity contamination guard for script generation
#
# Prevents cross-contamination between subjects (e.g. Dahmer content leaking
# into a Gacy script). Safe to import from script_agent at any time.
#
# Public API:
#   build_active_entity(topic)           -> dict
#   validate_entity_consistency(script)  -> (bool, list[str])
#   sanitize_script(script, active)      -> str
#   ENTITY_LOCK_INSTRUCTION              -> str  (inject into prompts)
#   is_single_subject(topic)             -> bool

import re

# ── Well-known serial killer / criminal pool ──────────────────────────────────
# Any name from this set that is NOT the active subject is a forbidden entity.
_KNOWN_CRIMINALS = [
    "Jeffrey Dahmer", "Dahmer",
    "John Wayne Gacy", "Gacy",
    "Ted Bundy", "Bundy",
    "Richard Ramirez", "Night Stalker",
    "Ed Kemper", "Kemper",
    "Ed Gein", "Gein",
    "Charles Manson", "Manson",
    "Dennis Rader", "BTK",
    "Gary Ridgway", "Green River Killer",
    "Aileen Wuornos", "Wuornos",
    "Andrei Chikatilo", "Chikatilo",
    "Jack the Ripper",
    "David Berkowitz", "Son of Sam",
    "Albert Fish",
    "H.H. Holmes",
    "Robert Pickton",
    "Pedro Alonso Lopez",
    "Zodiac Killer",
    "Randy Kraft",
    "Dean Corll",
    "John Edward Robinson",
    "Paul Bernardo",
    "Marc Dutroux",
    "Ahmad Suradji",
    # Organised crime (overlap with other topics kept intentionally)
    "Pablo Escobar", "Escobar",
    "El Chapo", "Chapo",
    "Griselda Blanco",
    "Al Capone", "Capone",
    "John Gotti", "Gotti",
    "Whitey Bulger", "Bulger",
    "Henry Hill",
    "Lucky Luciano",
    "Frank Lucas",
    "Carlos Lehder",
]


def _normalise(name: str) -> str:
    return name.strip().lower()


def build_active_entity(topic: str) -> dict:
    """
    Return an entity dict for the given topic string.

    {
      "canonical_name": "Jeffrey Dahmer",
      "aliases": ["Dahmer", "Jeffrey Dahmer"],
      "blocked_entities": [...all other known criminals...]
    }

    The caller should store this dict and pass it to validate_entity_consistency
    and sanitize_script for every generated section.
    """
    canonical = topic.strip()
    topic_lower = canonical.lower()

    aliases: list[str] = [canonical]
    # Add single-surname alias for multi-word names
    parts = canonical.split()
    if len(parts) >= 2:
        aliases.append(parts[-1])  # surname
        aliases.append(parts[0])   # first name (useful for short references)

    aliases = list(dict.fromkeys(aliases))  # deduplicate, preserve order

    blocked: list[str] = []
    alias_lowers = {_normalise(a) for a in aliases}

    for name in _KNOWN_CRIMINALS:
        if _normalise(name) not in alias_lowers and name not in blocked:
            # Skip if it is a partial substring of the active topic
            if _normalise(name) not in topic_lower and topic_lower not in _normalise(name):
                blocked.append(name)

    return {
        "canonical_name": canonical,
        "aliases": aliases,
        "blocked_entities": blocked,
    }


def is_single_subject(topic: str) -> bool:
    """
    Return True when the topic is a single-subject script that needs strict
    entity locking.  Returns False for multi-subject topics like comparisons,
    crossovers, or "top X" style content where multiple names are expected.
    """
    t = topic.lower()
    multi_signals = [
        " vs ", " versus ", " and ", " & ",
        "top ", "best ", "worst ",
        "comparison", "crossover", "ranking",
    ]
    return not any(sig in t for sig in multi_signals)


def validate_entity_consistency(script: str, active_entity: dict) -> tuple[bool, list[str]]:
    """
    Scan *script* for forbidden entity names.

    Returns:
        (passed: bool, offending_lines: list[str])
        passed=True  → [EntityGuard] PASS logged
        passed=False → [EntityGuard] FAIL logged with each offending line
    """
    blocked = active_entity.get("blocked_entities", [])
    canonical = active_entity.get("canonical_name", "")
    offending: list[str] = []

    lines = script.splitlines()
    # Count occurrences per forbidden entity across the full script
    entity_counts: dict[str, int] = {}

    for entity in blocked:
        pattern = re.compile(r'\b' + re.escape(entity) + r'\b', re.IGNORECASE)
        matches = pattern.findall(script)
        if matches:
            entity_counts[entity] = len(matches)

    if not entity_counts:
        print(f"[EntityGuard] PASS: {canonical}")
        return True, []

    # Collect offending lines for each entity found more than once
    for entity, count in entity_counts.items():
        if count > 1:
            pattern = re.compile(r'\b' + re.escape(entity) + r'\b', re.IGNORECASE)
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    offending.append(f"Line {i}: {line.strip()[:120]}")
            print(f"[EntityGuard] FAIL: Found forbidden entity \"{entity}\" {count}x in script about {canonical}")
        elif count == 1:
            # Single mention is contextual (e.g. comparison reference) — warn only
            print(f"[EntityGuard] WARN: \"{entity}\" appears once in script about {canonical} — may be a comparison reference")

    passed = len([e for e, c in entity_counts.items() if c > 1]) == 0
    if passed:
        print(f"[EntityGuard] PASS: {canonical}")
    return passed, offending


def sanitize_script(script: str, active_entity: dict) -> str:
    """
    Remove paragraphs that contain a forbidden entity appearing more than once.
    Single-occurrence mentions are kept (they may be valid comparison references).

    Returns the sanitised script.  Logs each removed paragraph.
    """
    blocked = active_entity.get("blocked_entities", [])
    canonical = active_entity.get("canonical_name", "")

    paragraphs = re.split(r'\n{2,}', script)
    clean_paragraphs: list[str] = []

    for para in paragraphs:
        contaminated = False
        for entity in blocked:
            pattern = re.compile(r'\b' + re.escape(entity) + r'\b', re.IGNORECASE)
            if len(pattern.findall(para)) > 1:
                print(f"[EntityGuard] REMOVED paragraph containing \"{entity}\" (topic={canonical}): "
                      f"{para[:80].strip()}...")
                contaminated = True
                break
        if not contaminated:
            clean_paragraphs.append(para)

    result = "\n\n".join(clean_paragraphs)
    if len(clean_paragraphs) < len(paragraphs):
        removed = len(paragraphs) - len(clean_paragraphs)
        print(f"[EntityGuard] Sanitised {removed} paragraph(s) from script about {canonical}")
    return result


def entity_lock_instruction(active_entity: dict) -> str:
    """
    Return a hard instruction string to inject at the top of every LLM prompt.

    Safe to call with an empty dict — returns a no-op empty string.
    """
    if not active_entity:
        return ""
    canonical = active_entity.get("canonical_name", "")
    aliases = active_entity.get("aliases", [canonical])
    blocked = active_entity.get("blocked_entities", [])

    # Only show the first 8 blocked names in the prompt to avoid bloating token count
    blocked_preview = blocked[:8]
    blocked_str = ", ".join(f'"{b}"' for b in blocked_preview)
    if len(blocked) > 8:
        blocked_str += f" (and {len(blocked) - 8} more)"

    return (
        f"\n\n[ENTITY LOCK] ACTIVE SUBJECT: {canonical}\n"
        f"You are writing ONLY about: {', '.join(aliases)}\n"
        f"STRICTLY FORBIDDEN - do NOT reference: {blocked_str}\n"
        f"RULE: If any forbidden name appears in your output more than once, "
        f"the generation will be rejected and regenerated.\n"
        f"ONLY discuss the active subject. "
        f"Do NOT reference unrelated serial killers or crime cases unless explicitly requested.\n"
    )
