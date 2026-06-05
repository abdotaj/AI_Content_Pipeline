# agents/character_pipeline.py
# ============================================================
#  CHARACTER PIPELINE
#  Separates research / entity extraction / image retrieval
#  from AI generation.  Used by ANIMATION, FULL, and FAST modes.
#
#  Architecture:
#    research
#    → extract_story_entities()   — LLM + regex, role-classified
#    → reconcile_entities()       — canonical ID + registry authority
#    → fetch_real_image()         — multi-stage aggressive retrieval
#    → build_episode_cast()       — assembles locked cast
#    → save/load_character_memory — per-episode persistence
#    → character_registry.json   — cross-run identity store (source of truth)
#
#  Identity locking guarantee:
#    Once a real portrait is locked (image_locked=True in registry), it is
#    NEVER replaced automatically. The same canonical_id resolves to the same
#    portrait, aliases, and role across all reruns and episodes.
#
#  Public API:
#    extract_story_entities(research, topic, script_text="") -> list[dict]
#    reconcile_entities(entities, topic, research)           -> list[dict]
#    normalize_entity_name(raw_name, topic="")              -> str
#    fetch_real_image(entity, output_dir)                    -> str | None
#    build_episode_cast(research, topic, output_dir)         -> dict[str, dict]
#    save_character_memory(topic, cast)                      -> None
#    load_character_memory(topic)                            -> dict | None
# ============================================================

import os
import re
import json
import time
import hashlib
import datetime
import requests
from typing import Any

try:
    from config import GROQ_API_KEY
    from groq import Groq
    _groq = Groq(api_key=GROQ_API_KEY)
except Exception:
    _groq = None

# ── Role constants ────────────────────────────────────────────────────────────
ROLE_SUSPECT    = "suspect"
ROLE_DETECTIVE  = "detective"
ROLE_VICTIM     = "victim"
ROLE_WITNESS    = "witness"
ROLE_JUDGE      = "judge"
ROLE_LAWYER     = "lawyer"
ROLE_FAMILY     = "family"
ROLE_REPORTER   = "reporter"
ROLE_INVESTIGATOR = "investigator"

# Maps pipeline role keys → entity roles (for backward compat with cast dict)
_PIPELINE_ROLE_MAP: dict[str, str] = {
    "main":       ROLE_SUSPECT,
    "detective":  ROLE_DETECTIVE,
    "victim":     ROLE_VICTIM,
    "witness":    ROLE_WITNESS,
}

# Role → DDG query suffix variants
_ROLE_QUERY_HINTS: dict[str, list[str]] = {
    ROLE_SUSPECT:     ["mugshot", "arrest photo", "criminal portrait", "real photo"],
    ROLE_DETECTIVE:   ["detective", "police officer", "FBI agent", "law enforcement official"],
    ROLE_VICTIM:      ["victim", "missing person", "memorial portrait", "real photo"],
    ROLE_WITNESS:     ["testimony", "interview", "real photo portrait"],
    ROLE_JUDGE:       ["judge portrait", "official photo"],
    ROLE_LAWYER:      ["attorney portrait", "official photo"],
    ROLE_FAMILY:      ["real photo", "family portrait"],
    ROLE_REPORTER:    ["journalist", "reporter photo"],
    ROLE_INVESTIGATOR:["investigator", "detective", "official photo"],
}

# Keywords in research facts that signal each role
_ROLE_KEYWORDS: dict[str, list[str]] = {
    ROLE_DETECTIVE:    ["detective", "agent", "inspector", "officer", "investigator",
                        "fbi", "cia", "dea", "sheriff", "marshal", "sergeant", "prosecutor"],
    ROLE_VICTIM:       ["victim", "murdered", "killed", "found dead", "disappeared",
                        "abducted", "missing", "slain", "body of", "remains of"],
    ROLE_WITNESS:      ["witness", "testified", "told police", "neighbor", "friend said",
                        "coworker said", "came forward"],
    ROLE_JUDGE:        ["judge", "justice", "presiding", "sentenced", "ruled"],
    ROLE_LAWYER:       ["attorney", "defense counsel", "prosecutor", "district attorney", "public defender"],
    ROLE_FAMILY:       ["father", "mother", "brother", "sister", "son", "daughter",
                        "wife", "husband", "parent", "sibling", "child"],
    ROLE_REPORTER:     ["journalist", "reporter", "correspondent", "editor", "news anchor"],
}

# Characters storage dir (legacy; also in content/<topic>/characters/)
_CHARS_DIR = "output/animation/characters"
os.makedirs(_CHARS_DIR, exist_ok=True)


# ============================================================
#  IDENTITY UTILITIES  —  canonical ID, normalization, fuzzy match
# ============================================================

def _canonical_id(name: str) -> str:
    """
    Deterministic slug from a canonical name.
    "Jeffrey Dahmer" → "jeffrey_dahmer"
    """
    slug = name.lower().strip()
    slug = re.sub(r"['''’]", "", slug)         # apostrophes
    slug = re.sub(r"[^a-z0-9\s]", " ", slug)        # collapse punctuation
    slug = re.sub(r"\s+", "_", slug.strip())         # spaces → underscores
    return slug[:60]


def _normalize_name(raw: str) -> str:
    """Normalize for matching: lowercase, strip punctuation, collapse whitespace."""
    n = raw.lower().strip()
    n = re.sub(r"['''’]", "", n)
    n = re.sub(r"[^a-z\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _fuzzy_similarity(a: str, b: str) -> float:
    """
    Bigram-based similarity between two names. Returns 0.0–1.0.
    No external dependencies.
    """
    def bigrams(s: str) -> set:
        s = _normalize_name(s)
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) >= 2 else set()

    if _normalize_name(a) == _normalize_name(b):
        return 1.0
    a_bg, b_bg = bigrams(a), bigrams(b)
    if not a_bg or not b_bg:
        return 0.0
    return (2 * len(a_bg & b_bg)) / (len(a_bg) + len(b_bg))


def _is_alias_match(name: str, existing: dict) -> bool:
    """
    Return True if name matches the existing registry entry's canonical name or aliases.
    Also catches abbreviated forms: "Jeff Dahmer" matches "Jeffrey Dahmer".
    """
    name_n = _normalize_name(name)
    if name_n == _normalize_name(existing.get("canonical_name", "")):
        return True
    for alias in existing.get("aliases", []):
        if name_n == _normalize_name(alias):
            return True
    # Abbreviated-form: same last name + first name prefix overlap
    n_parts = name_n.split()
    c_parts = _normalize_name(existing.get("canonical_name", "")).split()
    if len(n_parts) >= 2 and len(c_parts) >= 2:
        if n_parts[-1] == c_parts[-1]:
            if (n_parts[0] == c_parts[0]
                    or c_parts[0].startswith(n_parts[0])
                    or n_parts[0].startswith(c_parts[0])):
                return True
    return False


def normalize_entity_name(raw_name: str, topic: str = "") -> str:
    """
    Public utility: normalize a raw name to its canonical form.
    Checks the topic's character registry first; falls back to best-effort normalization.

    e.g. normalize_entity_name("Jeff Dahmer", topic="Jeffrey Dahmer Story")
         → "Jeffrey Dahmer"
    """
    if not raw_name:
        return raw_name
    registry = _load_character_registry(topic) if topic else {}
    for entry in registry.values():
        if _is_alias_match(raw_name, entry):
            return entry["canonical_name"]
    # No registry hit — normalize in-place (title-case)
    return " ".join(w.capitalize() for w in raw_name.strip().split())


# ============================================================
#  CHARACTER REGISTRY  —  cross-run identity source of truth
# ============================================================

def _load_character_registry(topic: str) -> dict:
    """
    Load the persistent identity registry for a topic.
    Returns {} if not found or unreadable.
    """
    if not topic:
        return {}
    chars_dir = _characters_dir(topic)
    path = os.path.join(chars_dir, "character_registry.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_character_registry(topic: str, registry: dict) -> None:
    chars_dir = _characters_dir(topic)
    path = os.path.join(chars_dir, "character_registry.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[CHARACTER] Registry save failed: {e}")


def _reconcile_entity(name: str, registry: dict) -> tuple[str, dict]:
    """
    Find the best matching registry entry for name.
    Returns (event_type, entry):
      "exact_match"  — canonical_id hit
      "alias_match"  — alias or abbreviated form matched
      "fuzzy_match"  — bigram similarity ≥ 0.82
      "new_entity"   — no match found
    """
    cid = _canonical_id(name)
    if cid in registry:
        return "exact_match", registry[cid]

    for entry in registry.values():
        if _is_alias_match(name, entry):
            return "alias_match", entry

    best_score, best_entry = 0.0, None
    for entry in registry.values():
        s = _fuzzy_similarity(name, entry.get("canonical_name", ""))
        if s > best_score:
            best_score, best_entry = s, entry
        for alias in entry.get("aliases", []):
            s = _fuzzy_similarity(name, alias)
            if s > best_score:
                best_score, best_entry = s, entry

    if best_score >= 0.82 and best_entry:
        return "fuzzy_match", best_entry

    return "new_entity", {}


def reconcile_entities(
    entities: list[dict],
    topic: str,
    research: dict,
) -> list[dict]:
    """
    Reconcile freshly extracted entities against the character registry.

    - Main subject (research.real_person) always gets confidence = 1.0 and locks first.
    - Existing registry entries are reused (canonical_name, portrait, aliases).
    - New aliases are merged into existing entries.
    - Low-confidence new entities (< 0.5) are dropped to prevent registry pollution.
    - Returns reconciled list with canonical_id, canonical_name, confidence,
      locked_image, image_locked added to each entity.
    """
    registry = _load_character_registry(topic)
    main_person = (research.get("real_person") or "").strip()
    today = datetime.date.today().isoformat()

    # Main subject always goes first with confidence 1.0
    def _sort_key(e: dict) -> float:
        if main_person and _normalize_name(e["name"]) == _normalize_name(main_person):
            return 2.0
        return float(e.get("confidence", e.get("importance", 0.5)))

    entities = sorted(entities, key=_sort_key, reverse=True)
    reconciled: list[dict] = []

    for entity in entities:
        name = entity["name"].strip()
        confidence = float(entity.get("confidence", entity.get("importance", 0.5)))
        if main_person and _normalize_name(name) == _normalize_name(main_person):
            confidence = 1.0

        event, existing = _reconcile_entity(name, registry)
        cid = _canonical_id(name)

        if event in ("exact_match", "alias_match", "fuzzy_match"):
            existing_cid = existing.get("canonical_id", _canonical_id(existing.get("canonical_name", name)))
            canonical_name = existing.get("canonical_name", name)
            print(f"[CHARACTER] Existing identity matched: {name!r} → {canonical_name!r} ({event})")

            if name != canonical_name and name not in existing.get("aliases", []):
                existing.setdefault("aliases", []).append(name)
                print(f"[CHARACTER] Alias merged: {name!r} into {canonical_name!r}")

            # Reuse locked portrait if still valid on disk
            locked_img = None
            if existing.get("image_locked") and existing.get("ref_image_path"):
                if os.path.exists(existing["ref_image_path"]):
                    locked_img = existing["ref_image_path"]
                    print(f"[CHARACTER] Portrait reused: {canonical_name!r}")
                else:
                    existing["image_locked"] = False  # stale path — allow re-fetch

            existing["last_seen_topic"] = topic
            registry[existing_cid] = existing

            reconciled.append({
                **entity,
                "name":           canonical_name,
                "canonical_id":   existing_cid,
                "canonical_name": canonical_name,
                "aliases":        existing.get("aliases", []),
                "confidence":     confidence,
                "locked_image":   locked_img,
                "image_locked":   existing.get("image_locked", False),
            })

        else:  # new_entity
            if confidence < 0.5:
                print(f"[CHARACTER] Low-confidence entity skipped: {name!r} (conf={confidence:.2f})")
                continue
            print(f"[CHARACTER] New identity created: {name!r} (role={entity.get('role','?')}, conf={confidence:.2f})")
            new_entry = {
                "canonical_id":    cid,
                "canonical_name":  name,
                "aliases":         entity.get("aliases", []),
                "role":            entity.get("role", ROLE_SUSPECT),
                "gender":          entity.get("gender", "unknown"),
                "era":             entity.get("era", ""),
                "context":         entity.get("context", ""),
                "confidence":      confidence,
                "ref_image_path":  None,
                "image_locked":    False,
                "image_source":    "none",
                "locked_at":       None,
                "last_seen_topic": topic,
                "created_at":      today,
            }
            registry[cid] = new_entry
            reconciled.append({
                **entity,
                "canonical_id":   cid,
                "canonical_name": name,
                "confidence":     confidence,
                "locked_image":   None,
                "image_locked":   False,
            })

    _save_character_registry(topic, registry)
    return reconciled


def _lock_portrait_in_registry(
    topic: str,
    canonical_id: str,
    img_path: str,
    image_source: str,
) -> None:
    """Permanently lock a portrait in the character registry."""
    registry = _load_character_registry(topic)
    if canonical_id not in registry:
        return
    entry = registry[canonical_id]
    entry["ref_image_path"] = img_path
    entry["image_locked"]   = True
    entry["image_source"]   = image_source
    entry["locked_at"]      = datetime.date.today().isoformat()
    registry[canonical_id]  = entry
    _save_character_registry(topic, registry)
    print(f"[CHARACTER] Canonical ID locked: {canonical_id!r} (source={image_source})")


# ============================================================
#  STEP 1: ENTITY EXTRACTION
#  OpenAI = PRIMARY authority   |   Groq = SECONDARY fallback
# ============================================================

# OpenAI client (lazy-init to avoid import error if not installed)
_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    try:
        from openai import OpenAI
        import os as _os
        _openai_client = OpenAI(api_key=_os.getenv("OPENAI_API_KEY"))
        return _openai_client
    except Exception:
        return None


# Strict JSON schema that OpenAI must return
_ENTITY_SCHEMA_EXAMPLE = (
    '[{"canonical_id":"jeffrey_dahmer","canonical_name":"Jeffrey Dahmer",'
    '"aliases":["Jeff Dahmer","Dahmer"],"role":"suspect","gender":"male",'
    '"era":"1978–1991","importance":1.0,"confidence":0.95,'
    '"context":"Serial killer convicted of 17 murders in Milwaukee, 1991"}]'
)

_ENTITY_SYSTEM_PROMPT = (
    "You are an identity resolution engine for a true crime documentary pipeline. "
    "Extract ALL named real-world people. Normalize name variants into one canonical form. "
    "Merge aliases. Assign stable canonical_id (snake_case from canonical_name). "
    "Return ONLY a valid JSON array. No prose. No markdown."
)


def extract_story_entities(
    research: dict,
    topic: str,
    script_text: str = "",
) -> list[dict]:
    """
    Extract all named real-world people from the research + script.

    Primary:   OpenAI gpt-4.1-mini (authoritative identity resolver)
    Secondary: Groq llama-3.3-70b-versatile (fallback extractor)
    Tertiary:  Regex (last resort)

    Each returned entity has:
      canonical_id, canonical_name, name, aliases, role,
      gender, era, importance, confidence, context
    """
    facts_raw = (
        (research.get("research_facts") or [])
        + (research.get("real_facts") or [])
        + [str((research.get("verified_facts") or {}).get("story", ""))]
        + (research.get("research_shocking") or [])
    )
    era = (research.get("verified_facts") or {}).get("time_period", "") or ""

    # ── Stage 1: OpenAI (primary authority) ──────────────────────────────────
    openai_entities = _openai_extract_entities(topic, facts_raw, script_text, era)
    if openai_entities:
        print(f"[CHARACTER] OpenAI normalization applied: {len(openai_entities)} entities for '{topic}'")
        for e in openai_entities:
            print(f"[CHARACTER] [{e.get('role','?').upper()}] {e.get('canonical_name', e.get('name','?'))}")
        return openai_entities

    # ── Stage 2: Groq (fallback extractor) ───────────────────────────────────
    print(f"[CHARACTER] Groq fallback activated for '{topic}'")
    groq_entities: list[dict] = []
    if _groq:
        groq_entities = _groq_extract_entities(topic, facts_raw, script_text, era)

    if groq_entities:
        # Run OpenAI reconciliation pass on Groq output if OpenAI is available
        reconciled = _openai_reconciliation_pass(groq_entities, topic, era)
        if reconciled:
            print(f"[CHARACTER] OpenAI normalization applied (Groq→OpenAI pass): {len(reconciled)} entities")
            return reconciled
        print(f"[CHARACTER] Groq extraction (no OpenAI pass): {len(groq_entities)} entities")
        for e in groq_entities:
            print(f"[CHARACTER] [{e.get('role','?').upper()}] {e.get('name','?')}")
        return groq_entities

    # ── Stage 3: Regex (last resort) ─────────────────────────────────────────
    print(f"[CHARACTER] All LLMs unavailable — using regex extraction for '{topic}'")
    return _regex_extract_entities(topic, facts_raw, research)


def _openai_extract_entities(
    topic: str,
    facts: list[str],
    script_text: str,
    era: str,
) -> list[dict]:
    """OpenAI gpt-4.1-mini: primary identity extraction with normalization."""
    oai = _get_openai_client()
    if not oai:
        return []

    facts_text = "\n".join(f"- {f}" for f in facts[:14]) or "(no facts)"
    script_sample = script_text[:1000] if script_text else ""

    prompt = (
        f"Extract ALL named real-world people from this true crime documentary topic.\n\n"
        f"TOPIC: {topic}\n"
        f"ERA: {era or 'unknown'}\n\n"
        f"RESEARCH FACTS:\n{facts_text}\n\n"
        + (f"SCRIPT EXCERPT:\n{script_sample}\n\n" if script_sample else "")
        + "For each person return this exact schema:\n"
        "- canonical_id: snake_case slug from canonical_name (e.g. jeffrey_dahmer)\n"
        "- canonical_name: normalized full name (merge Jeff/Jeffrey/Dahmer → Jeffrey Dahmer)\n"
        "- aliases: list of name variants found in the text\n"
        "- role: one of [suspect, detective, victim, witness, judge, lawyer, family, reporter, investigator]\n"
        "- gender: male/female/unknown\n"
        "- era: approximate period (e.g. '1978–1991')\n"
        "- importance: 0.0–1.0 (main subject = 1.0)\n"
        "- confidence: 0.0–1.0 (how certain you are this is a distinct real person)\n"
        "- context: one-sentence description of their role in this story\n\n"
        "CRITICAL: Merge name variants into ONE entry. Do NOT create separate entries for "
        "abbreviated forms (Jeff vs Jeffrey). Return ONLY a valid JSON array. Max 8 people. "
        f"Most important first.\nSchema example:\n{_ENTITY_SCHEMA_EXAMPLE}"
    )
    try:
        resp = oai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": _ENTITY_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=1000,
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_entity_json(raw, source="openai")
    except Exception as ex:
        print(f"[CHARACTER] OpenAI extraction failed: {ex}")
        return []


def _openai_reconciliation_pass(
    entities: list[dict],
    topic: str,
    era: str,
) -> list[dict]:
    """
    Run OpenAI normalization on a list already extracted by Groq.
    Merges aliases, normalizes names, adds canonical_id + confidence.
    Returns [] if OpenAI unavailable (caller keeps Groq output).
    """
    oai = _get_openai_client()
    if not oai:
        return []

    raw_names = json.dumps(
        [{"name": e.get("name", ""), "role": e.get("role", "?"),
          "aliases": e.get("aliases", []), "context": e.get("context", "")}
         for e in entities],
        ensure_ascii=False
    )
    prompt = (
        f"Normalize these character extractions from topic: {topic!r} (era: {era or 'unknown'})\n\n"
        f"RAW EXTRACTIONS:\n{raw_names}\n\n"
        "For each person: merge aliases, resolve canonical full name, assign canonical_id. "
        "Add confidence 0.0–1.0. Return ONLY a valid JSON array using the exact schema:\n"
        f"{_ENTITY_SCHEMA_EXAMPLE}"
    )
    try:
        resp = oai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": _ENTITY_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=800,
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_entity_json(raw, source="openai-reconciliation")
    except Exception as ex:
        print(f"[CHARACTER] OpenAI reconciliation pass failed: {ex}")
        return []


def _parse_entity_json(raw: str, source: str = "llm") -> list[dict]:
    """Parse and validate an entity JSON array from any LLM response."""
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        return []
    try:
        entities = json.loads(m.group())
    except json.JSONDecodeError:
        return []
    if not isinstance(entities, list):
        return []

    valid = []
    valid_roles = {ROLE_SUSPECT, ROLE_DETECTIVE, ROLE_VICTIM, ROLE_WITNESS,
                   ROLE_JUDGE, ROLE_LAWYER, ROLE_FAMILY, ROLE_REPORTER, ROLE_INVESTIGATOR}
    for e in entities:
        if not isinstance(e, dict):
            continue
        # Support both "canonical_name" (OpenAI schema) and "name" (Groq schema)
        canonical_name = (e.get("canonical_name") or e.get("name") or "").strip()
        if not canonical_name:
            continue
        cid = e.get("canonical_id") or _canonical_id(canonical_name)
        role = str(e.get("role", ROLE_SUSPECT)).lower().strip()
        if role not in valid_roles:
            role = ROLE_SUSPECT
        valid.append({
            "canonical_id":   cid,
            "canonical_name": canonical_name,
            "name":           canonical_name,  # always canonical after normalization
            "aliases":        [str(a) for a in (e.get("aliases") or []) if a],
            "role":           role,
            "gender":         str(e.get("gender", "unknown")).lower(),
            "era":            str(e.get("era", "") or ""),
            "importance":     float(e.get("importance", 0.5)),
            "confidence":     float(e.get("confidence", e.get("importance", 0.5))),
            "context":        str(e.get("context", "") or ""),
            "_source":        source,
        })
    return sorted(valid, key=lambda x: x["importance"], reverse=True)


def _groq_extract_entities(
    topic: str,
    facts: list[str],
    script_text: str,
    era: str,
) -> list[dict]:
    """Groq llama-3.3-70b: secondary fallback extractor. Never authoritative for identity."""
    if not _groq:
        return []
    facts_text = "\n".join(f"- {f}" for f in facts[:12]) or "(no facts)"
    script_sample = script_text[:800] if script_text else ""

    prompt = (
        f"Extract ALL named real-world people from this true crime story.\n\n"
        f"TOPIC: {topic}\nERA: {era or 'unknown'}\n\n"
        f"RESEARCH FACTS:\n{facts_text}\n\n"
        + (f"SCRIPT EXCERPT:\n{script_sample}\n\n" if script_sample else "")
        + "For each person return:\n"
        "- name: canonical full name\n"
        "- role: one of [suspect, detective, victim, witness, judge, lawyer, family, reporter, investigator]\n"
        "- aliases: list of alternate names/nicknames\n"
        "- gender: male/female/unknown\n"
        "- era: approximate period\n"
        "- importance: 0.0–1.0 (main subject = 1.0)\n"
        "- confidence: 0.0–1.0\n"
        "- context: one-sentence description\n\n"
        "Return ONLY a valid JSON array. Max 8 people. Most important first.\n"
        f"Example: {_ENTITY_SCHEMA_EXAMPLE}"
    )
    try:
        resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content":
                 "You extract structured data from text. Return only valid JSON arrays."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_entity_json(raw, source="groq")
    except Exception as ex:
        print(f"[CHARACTER] Groq extraction failed: {ex}")
        return []


def _regex_extract_entities(
    topic: str,
    facts: list[str],
    research: dict,
) -> list[dict]:
    """Last-resort: pattern-match proper names from facts, classify by role keywords."""
    main_name = (research.get("real_person") or topic).strip()
    era = (research.get("verified_facts") or {}).get("time_period", "") or ""

    entities: dict[str, dict] = {}

    # Main subject always first with highest confidence
    cid_main = _canonical_id(main_name)
    entities[main_name.lower()] = {
        "canonical_id":   cid_main,
        "canonical_name": main_name,
        "name":           main_name,
        "role":           ROLE_SUSPECT,
        "aliases":        [],
        "gender":         "unknown",
        "era":            era,
        "importance":     1.0,
        "confidence":     0.9,
        "context":        f"Main subject of: {topic}",
        "_source":        "regex",
    }

    for fact in facts:
        names_in_fact = re.findall(
            r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)\b', fact
        )
        fact_lower = fact.lower()
        for name in names_in_fact:
            if name.lower() == main_name.lower():
                continue
            if name.lower() in entities:
                continue
            role = ROLE_WITNESS
            for r, kws in _ROLE_KEYWORDS.items():
                if any(kw in fact_lower for kw in kws):
                    role = r
                    break
            cid = _canonical_id(name)
            entities[name.lower()] = {
                "canonical_id":   cid,
                "canonical_name": name,
                "name":           name,
                "role":           role,
                "aliases":        [],
                "gender":         "unknown",
                "era":            era,
                "importance":     0.5,
                "confidence":     0.4,  # regex hits are lower confidence
                "context":        fact[:100],
                "_source":        "regex",
            }
            if len(entities) >= 8:
                break
        if len(entities) >= 8:
            break

    return sorted(entities.values(), key=lambda x: x["importance"], reverse=True)


# ============================================================
#  STEP 2: MULTI-STAGE REAL IMAGE RETRIEVAL
# ============================================================

def fetch_real_image(
    entity: dict,
    output_dir: str,
) -> str | None:
    """
    Aggressive multi-stage real image retrieval for a named entity.

    Search order:
      Stage 1 — Wikipedia REST API (thumbnail + originalimage)
      Stage 2 — Wikimedia Commons (multiple query variants)
      Stage 3 — DDG images (role-specific queries + aliases)
      Stage 4 — (caller's responsibility: AI reconstruction)

    Returns saved local path or None.
    """
    name    = entity.get("name", "").strip()
    role    = entity.get("role", ROLE_SUSPECT)
    aliases = entity.get("aliases") or []
    era     = entity.get("era", "")
    if not name:
        return None

    os.makedirs(output_dir, exist_ok=True)
    slug = re.sub(r'[^a-z0-9_]', '_', name.lower())[:40]
    out  = os.path.join(output_dir, f"{role}_{slug}.jpg")

    # Skip if already downloaded in this run
    if os.path.exists(out) and os.path.getsize(out) > 10_000:
        print(f"[IMAGE] Cached image reused: {name} → {os.path.basename(out)}")
        return out

    # Build candidate name list: canonical + abbreviated + aliases
    parts = name.split()
    candidates = [name]
    if len(parts) >= 2:
        candidates.append(f"{parts[0]} {parts[-1]}")  # first + last only
    candidates += [str(a) for a in aliases if a]
    candidates = list(dict.fromkeys(candidates))  # dedup

    print(f"[IMAGE] Searching {len(candidates)} name variant(s) for: {name} (role={role})")

    # ── Stage 1: Wikipedia ────────────────────────────────────────────────────
    for cand in candidates:
        path = _wikipedia_portrait(cand, out)
        if path:
            print(f"[IMAGE] Wikipedia portrait locked: {name} → {os.path.basename(path)}")
            return path

    # ── Stage 2: Wikimedia Commons ────────────────────────────────────────────
    for cand in candidates:
        urls = _wikimedia_commons_search(cand, role, era)
        if urls:
            saved = _download_and_validate(urls, out)
            if saved:
                print(f"[IMAGE] Wikimedia Commons image locked: {name} → {os.path.basename(saved)}")
                return saved

    # ── Stage 3: DDG targeted image search ───────────────────────────────────
    for cand in candidates:
        urls = _ddg_portrait_search(cand, role, era)
        if urls:
            saved = _download_and_validate(urls, out)
            if saved:
                print(f"[IMAGE] DDG portrait locked: {name} → {os.path.basename(saved)}")
                return saved

    print(f"[IMAGE] No real image found for: {name} (role={role}) — AI reconstruction needed")
    return None


def _wikipedia_portrait(name: str, out: str) -> str | None:
    """Wikipedia REST summary API — returns thumbnail if available."""
    try:
        encoded = requests.utils.quote(name.replace(" ", "_"))
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}",
            timeout=10,
            headers={"User-Agent": "DarkCrimeDecoded/1.0 character-pipeline"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        # Prefer originalimage (higher res) over thumbnail
        img_url = (
            (data.get("originalimage") or {}).get("source")
            or (data.get("thumbnail") or {}).get("source")
            or ""
        )
        if img_url:
            return _save_portrait_image(img_url, out)
    except Exception as e:
        print(f"[IMAGE] Wikipedia failed ({name}): {e}")
    return None


def _wikimedia_commons_search(name: str, role: str, era: str) -> list[str]:
    """
    Search Wikimedia Commons for portrait-quality images.
    Tries multiple query strategies per name.
    """
    queries = [name]
    if era:
        queries.append(f"{name} {era[:4]}")  # e.g. "Jeffrey Dahmer 1991"
    role_hint = _ROLE_QUERY_HINTS.get(role, ["portrait"])[0]
    queries.append(f"{name} {role_hint}")

    urls: list[str] = []
    seen: set[str] = set()

    for q in queries:
        try:
            params = {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": q,
                "gsrlimit": 8,
                "prop": "imageinfo",
                "iiprop": "url|mediatype|size",
                "iiurlwidth": 800,
            }
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params=params,
                timeout=12,
                headers={"User-Agent": "DarkCrimeDecoded/1.0"},
            )
            if r.status_code != 200:
                continue
            pages = r.json().get("query", {}).get("pages", {}).values()
            for page in pages:
                ii   = (page.get("imageinfo") or [{}])[0]
                url  = ii.get("thumburl") or ii.get("url", "")
                mt   = ii.get("mediatype", "")
                size = ii.get("size", 0)
                if url and mt in ("BITMAP", "DRAWING") and size > 8000 and url not in seen:
                    urls.append(url)
                    seen.add(url)
        except Exception:
            pass
    return urls[:6]


def _ddg_portrait_search(name: str, role: str, era: str) -> list[str]:
    """DDG image search with role-specific query variants."""
    hints = _ROLE_QUERY_HINTS.get(role, ["real photo portrait"])
    queries = []
    for hint in hints[:3]:
        queries.append(f'"{name}" {hint}')
    if era:
        queries.append(f'"{name}" {era[:4]}')

    urls: list[str] = []
    seen: set[str] = set()
    try:
        import os as _os
        _ddg_proxy = _os.getenv("DDG_PROXY") or _os.getenv("HTTPS_PROXY") or None
        from duckduckgo_search import DDGS
        with DDGS(proxy=_ddg_proxy) as ddgs:
            for q in queries:
                try:
                    results = list(ddgs.images(q, max_results=6, safesearch="off"))
                    time.sleep(0.5)  # rate-limit courtesy
                except Exception:
                    continue
                for result in results:
                    url = result.get("image", "")
                    if url and url not in seen:
                        ext = url.lower().split("?")[0]
                        if any(ext.endswith(e) for e in (".jpg", ".jpeg", ".png")):
                            urls.append(url)
                            seen.add(url)
                if len(urls) >= 8:
                    break
    except ImportError:
        pass
    except Exception as e:
        print(f"[IMAGE] DDG search failed ({name}): {e}")

    return urls[:8]


def _save_portrait_image(url: str, out_path: str) -> str | None:
    """Download, validate, resize to portrait format, and save."""
    try:
        from PIL import Image as _PIL
        import io

        r = requests.get(
            url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200 or len(r.content) < 10_000:
            return None

        img = _PIL.open(io.BytesIO(r.content)).convert("RGB")
        w, h = img.size

        # Reject very small images — face won't be visible
        if w < 100 or h < 100:
            return None

        # Reject extreme landscape images (banners, logos)
        if w > h * 4:
            return None

        # Center-crop to portrait if landscape
        if w > h:
            crop_w = h
            left   = (w - crop_w) // 2
            img    = img.crop((left, 0, left + crop_w, h))

        img = img.resize((800, 1000), _PIL.LANCZOS)
        out_path = out_path if out_path.endswith(".jpg") else out_path.replace(".png", ".jpg")
        img.save(out_path, "JPEG", quality=92)
        return out_path
    except Exception:
        return None


def _download_and_validate(urls: list[str], out_path: str) -> str | None:
    """Try each URL until one downloads and validates as a usable portrait."""
    for url in urls:
        saved = _save_portrait_image(url, out_path)
        if saved:
            return saved
    return None


def _validate_portrait_image(path: str) -> bool:
    """Return True if the file at path is a valid, usable portrait image."""
    if not path or not os.path.exists(path):
        return False
    if os.path.getsize(path) < 8_000:
        return False
    try:
        from PIL import Image as _PIL
        img = _PIL.open(path)
        w, h = img.size
        return w >= 100 and h >= 100 and w < h * 5
    except Exception:
        return False


# ============================================================
#  STEP 3: BUILD EPISODE CAST
# ============================================================

_ROLE_PRIORITY: dict[str, str] = {
    ROLE_SUSPECT:      "main",
    ROLE_DETECTIVE:    "detective",
    ROLE_INVESTIGATOR: "detective",
    ROLE_VICTIM:       "victim",
    ROLE_WITNESS:      "witness",
    ROLE_JUDGE:        "witness",
    ROLE_LAWYER:       "witness",
    ROLE_FAMILY:       "witness",
    ROLE_REPORTER:     "witness",
}


def build_episode_cast(
    research: dict,
    topic: str,
    output_dir: str = _CHARS_DIR,
    script_text: str = "",
    style_preset: str = "default",
    style_keywords: str = "",
) -> dict[str, dict]:
    """
    Full character pipeline with identity locking:
      1. Memory cache check — registry-locked identities are authoritative
      2. OpenAI entity extraction (primary) → Groq (secondary) → Regex (last resort)
      3. Reconcile against character registry (merge aliases, reuse locked portraits)
      4. Fetch images only for unlocked entities
      5. Lock portraits permanently in registry after first successful fetch
      6. Assemble and persist cast

    Identity guarantee: once locked, same canonical_id → same portrait every run.
    """
    os.makedirs(output_dir, exist_ok=True)

    era  = (research.get("verified_facts") or {}).get("time_period", "") or ""
    locs = (research.get("verified_facts") or {}).get("real_locations", [])
    loc  = locs[0] if locs else ""

    # ── 1. Memory cache — registry-locked identities take priority ────────────
    cached = load_character_memory(topic)
    if cached:
        _valid = {k: v for k, v in cached.items()
                  if _validate_portrait_image(v.get("ref_image_path", ""))}
        if len(_valid) >= 1:
            print(f"[CHARACTER] Memory cache hit: {list(_valid.keys())} for '{topic}'")
            return _ensure_cast_completeness(
                _valid, research, topic, output_dir, style_preset, style_keywords
            )

    # ── 2. Extract entities (OpenAI primary, Groq fallback, regex last resort) ─
    entities = extract_story_entities(research, topic, script_text)

    # ── 3. Reconcile against character registry ───────────────────────────────
    entities = reconcile_entities(entities, topic, research)

    # ── 4. Build cast — reuse locked portraits, fetch only what's missing ──────
    cast: dict[str, dict] = {}
    used_roles: set[str] = set()

    for entity in entities:
        pipeline_role = _ROLE_PRIORITY.get(entity.get("role", ""), "witness")
        if pipeline_role in used_roles:
            continue
        used_roles.add(pipeline_role)

        canonical_id   = entity.get("canonical_id", _canonical_id(entity["name"]))
        canonical_name = entity.get("canonical_name", entity["name"])
        image_locked   = entity.get("image_locked", False)
        locked_img     = entity.get("locked_image")  # set by reconcile_entities

        if image_locked and locked_img:
            # Reuse permanently locked portrait — never re-scrape
            img_path     = locked_img
            image_source = "real_locked"
        else:
            # Fetch real image (portrait not yet locked or stale)
            img_path = fetch_real_image(entity, output_dir)
            if img_path:
                image_source = "real"
                # Lock the portrait permanently in registry
                _lock_portrait_in_registry(topic, canonical_id, img_path, "real")
            else:
                # AI reconstruction fallback — deterministic seed → same face every run
                img_path     = _ai_reconstruction_fallback(entity, output_dir, style_keywords)
                image_source = "ai_reconstruction" if img_path else "generic"
                if img_path:
                    _lock_portrait_in_registry(topic, canonical_id, img_path, "ai_reconstruction")

        descriptor = _build_entity_descriptor(entity, loc)
        cast[pipeline_role] = {
            "canonical_id":   canonical_id,
            "name":           canonical_name,
            "role":           entity.get("role", ROLE_SUSPECT),
            "ref_image_path": img_path,
            "descriptor":     descriptor,
            "era":            entity.get("era") or era or "historical documentary",
            "location":       loc or "unknown location",
            "style_preset":   style_preset,
            "style_keywords": style_keywords,
            "image_source":   image_source,
            "aliases":        entity.get("aliases", []),
            "gender":         entity.get("gender", "unknown"),
            "context":        entity.get("context", ""),
            "confidence":     entity.get("confidence", 0.5),
            "entity_type":    "real_person",
        }

    # ── 5. Ensure all pipeline roles are present ──────────────────────────────
    cast = _ensure_cast_completeness(
        cast, research, topic, output_dir, style_preset, style_keywords
    )

    # ── 6. Persist ────────────────────────────────────────────────────────────
    save_character_memory(topic, cast)

    _photos = sum(
        1 for v in cast.values()
        if v.get("ref_image_path") and os.path.exists(v.get("ref_image_path") or "")
    )
    print(f"[CHARACTER] Cast assembled: {list(cast.keys())} | real photos: {_photos}/{len(cast)}")
    for role, char in cast.items():
        src  = char.get("image_source", "?")
        conf = char.get("confidence", 0.0)
        print(f"[CHARACTER] [{role.upper()}] {char['name']} (conf={conf:.2f}) — source={src}")

    return cast


def _ensure_cast_completeness(
    cast: dict,
    research: dict,
    topic: str,
    output_dir: str,
    style_preset: str,
    style_keywords: str,
) -> dict[str, dict]:
    """Fill missing pipeline roles with generic descriptors."""
    era  = (research.get("verified_facts") or {}).get("time_period", "") or ""
    locs = (research.get("verified_facts") or {}).get("real_locations", [])
    loc  = locs[0] if locs else ""

    defaults = {
        "main": {
            "name":       research.get("real_person") or topic,
            "role":       ROLE_SUSPECT,
            "descriptor": f"{research.get('real_person') or topic}, {era}, {loc}",
            "context":    f"Main subject of: {topic}",
        },
        "detective": {
            "name":       "lead investigator",
            "role":       ROLE_DETECTIVE,
            "descriptor": f"law enforcement investigator, {era or 'modern era'}, official uniform",
            "context":    "Lead investigator on the case",
        },
        "victim": {
            "name":       "victim",
            "role":       ROLE_VICTIM,
            "descriptor": f"victim memorial portrait, {era or 'documentary'}, somber atmosphere",
            "context":    "Victim of the crime",
        },
        "witness": {
            "name":       "witness",
            "role":       ROLE_WITNESS,
            "descriptor": f"anonymous witness, {era or 'modern'}, documentary interview framing",
            "context":    "Key witness in the case",
        },
    }

    for pipeline_role, defaults_data in defaults.items():
        if pipeline_role not in cast:
            cast[pipeline_role] = {
                "ref_image_path": None,
                "era":            era or "historical documentary",
                "location":       loc or "unknown location",
                "style_preset":   style_preset,
                "style_keywords": style_keywords,
                "image_source":   "generic",
                "aliases":        [],
                "gender":         "unknown",
                "entity_type":    "generic",
                **defaults_data,
            }
    return cast


def _build_entity_descriptor(entity: dict, location: str) -> str:
    """Build a compact visual descriptor string for prompt injection."""
    parts = [entity["name"]]
    if entity.get("era"):
        parts.append(entity["era"])
    if location:
        parts.append(location)
    if entity.get("context"):
        # Extract concise role from context
        ctx = entity["context"].lower()
        for kw in ["serial killer", "detective", "officer", "victim", "witness",
                   "judge", "attorney", "reporter", "investigator"]:
            if kw in ctx:
                parts.append(kw)
                break
    return ", ".join(dict.fromkeys(parts))


def _ai_reconstruction_fallback(
    entity: dict,
    output_dir: str,
    style_keywords: str = "",
) -> str | None:
    """
    Generate a Pollinations AI reconstruction ONLY when no real image was found.
    Locks to entity-specific prompt so the same face is generated consistently.
    """
    name   = entity.get("name", "unknown")
    role   = entity.get("role", ROLE_SUSPECT)
    era    = entity.get("era", "")
    gender = entity.get("gender", "unknown")
    context = entity.get("context", "")

    slug = re.sub(r'[^a-z0-9_]', '_', name.lower())[:30]
    out  = os.path.join(output_dir, f"{role}_{slug}_reconstruct.jpg")

    if os.path.exists(out) and os.path.getsize(out) > 10_000:
        print(f"[CHARACTER] AI reconstruction reused: {name}")
        return out

    # Role-specific visual prompts for realistic appearance
    _role_prompts = {
        ROLE_SUSPECT:     "realistic portrait, neutral expression, slight tension",
        ROLE_DETECTIVE:   "official portrait, law enforcement, professional attire, confident expression",
        ROLE_VICTIM:      "memorial portrait, warm light, gentle expression, dignified",
        ROLE_WITNESS:     "candid documentary portrait, natural lighting, honest expression",
        ROLE_JUDGE:       "formal official portrait, judicial robes, authoritative expression",
        ROLE_LAWYER:      "professional portrait, business attire, composed expression",
        ROLE_FAMILY:      "natural portrait, family photograph style, emotional",
        ROLE_REPORTER:    "journalist portrait, professional, newsroom context",
    }

    role_style = _role_prompts.get(role, "realistic documentary portrait")
    gender_hint = "male" if gender == "male" else ("female" if gender == "female" else "person")
    era_hint    = era[:10] if era else "historical"

    prompt = (
        f"{gender_hint}, {era_hint}, {role_style}, {name}, {context[:80]}, "
        f"{style_keywords or 'dark cinematic documentary'}, "
        f"realistic photography, no text, portrait, 9:16"
    )
    # Deterministic seed from name hash → same AI face every run for this entity
    seed = int(hashlib.md5(name.encode()).hexdigest()[:8], 16) % 1000000

    try:
        encoded = requests.utils.quote(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=800&height=1000&seed={seed}&nologo=true"
        )
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and len(r.content) > 10_000:
            from PIL import Image as _PIL
            import io
            img = _PIL.open(io.BytesIO(r.content)).convert("RGB")
            img.save(out, "JPEG", quality=92)
            print(f"[CHARACTER] AI reconstruction locked (seed={seed}): {name}")
            return out
    except Exception as e:
        print(f"[CHARACTER] AI reconstruction failed ({name}): {e}")

    return None


# ============================================================
#  STEP 4: CHARACTER MEMORY (PERSISTENCE)
# ============================================================

def _characters_dir(topic: str) -> str:
    """Return path to content/<topic>/characters/, creating it if needed."""
    try:
        import sys as _sys
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from utils.content_manager import ensure_topic_content
        paths = ensure_topic_content(topic)
        chars = os.path.join(paths["path"], "characters")
    except Exception:
        chars = os.path.join("content", topic.lower().replace(" ", "_"), "characters")
    os.makedirs(chars, exist_ok=True)
    return chars


def save_character_memory(topic: str, cast: dict) -> None:
    """Persist cast to content/<topic>/characters/cast.json."""
    chars_dir = _characters_dir(topic)
    mem: dict[str, Any] = {}
    for role, char in cast.items():
        img_path = char.get("ref_image_path") or ""
        mem[role] = {
            "canonical_id":   char.get("canonical_id", _canonical_id(char.get("name", ""))),
            "name":           char.get("name", ""),
            "role":           char.get("role", ""),
            "aliases":        char.get("aliases", []),
            "gender":         char.get("gender", "unknown"),
            "era":            char.get("era", ""),
            "location":       char.get("location", ""),
            "descriptor":     char.get("descriptor", ""),
            "context":        char.get("context", ""),
            "confidence":     char.get("confidence", 0.5),
            "image_source":   char.get("image_source", ""),
            "ref_image_path": img_path,
        }
    cache_path = os.path.join(chars_dir, "cast.json")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2, ensure_ascii=False)
        print(f"[CHARACTER] Memory saved: {cache_path}")
    except Exception as e:
        print(f"[CHARACTER] Memory save failed: {e}")


def load_character_memory(topic: str) -> dict | None:
    """
    Load cast from content/<topic>/characters/cast.json.
    Validates image paths — drops stale ones so re-fetch can occur.
    Returns None if not found or empty.
    """
    chars_dir = _characters_dir(topic)
    cache_path = os.path.join(chars_dir, "cast.json")
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            mem = json.load(f)
        if not isinstance(mem, dict) or not mem:
            return None

        cast = {}
        for role, data in mem.items():
            img = data.get("ref_image_path", "")
            if img and not _validate_portrait_image(img):
                img = None
            cast[role] = {
                **data,
                "ref_image_path": img,
                "style_preset":   "default",
                "style_keywords": "",
                "entity_type":    "real_person",
            }
        print(f"[CHARACTER] Memory loaded: {list(cast.keys())} for '{topic}'")
        return cast if cast else None
    except Exception as e:
        print(f"[CHARACTER] Memory load failed: {e}")
        return None
