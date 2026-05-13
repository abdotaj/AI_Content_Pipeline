# agents/character_pipeline.py
# ============================================================
#  CHARACTER PIPELINE
#  Separates research / entity extraction / image retrieval
#  from AI generation.  Used by ANIMATION, FULL, and FAST modes.
#
#  Architecture:
#    research
#    → extract_story_entities()   — LLM + regex, role-classified
#    → fetch_real_image()         — multi-stage aggressive retrieval
#    → build_episode_cast()       — assembles locked cast
#    → save/load_character_memory — persistence in content/<topic>/characters/
#
#  Public API:
#    extract_story_entities(research, topic, script_text="") -> list[dict]
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
#  STEP 1: ENTITY EXTRACTION
# ============================================================

def extract_story_entities(
    research: dict,
    topic: str,
    script_text: str = "",
) -> list[dict]:
    """
    Extract all named real-world people from the research + script.

    Each entity dict:
      name, role, aliases, gender, era, importance (0-1), context

    Returns list sorted by importance descending.
    """
    facts_raw = (
        (research.get("research_facts") or [])
        + (research.get("real_facts") or [])
        + [str((research.get("verified_facts") or {}).get("story", ""))]
        + (research.get("research_shocking") or [])
    )
    era = (research.get("verified_facts") or {}).get("time_period", "") or ""

    # ── Stage 1: LLM extraction ──────────────────────────────────────────────
    llm_entities: list[dict] = []
    if _groq:
        llm_entities = _llm_extract_entities(topic, facts_raw, script_text, era)

    if llm_entities:
        print(f"[ENTITY] LLM extraction: {len(llm_entities)} entities for '{topic}'")
        for e in llm_entities:
            print(f"[ENTITY] {e.get('role','?').upper()}: {e.get('name','?')}")
        return llm_entities

    # ── Stage 2: Regex fallback ──────────────────────────────────────────────
    print(f"[ENTITY] LLM unavailable — using regex extraction for '{topic}'")
    return _regex_extract_entities(topic, facts_raw, research)


def _llm_extract_entities(
    topic: str,
    facts: list[str],
    script_text: str,
    era: str,
) -> list[dict]:
    """Use Groq to extract structured entity list from research facts."""
    facts_text = "\n".join(f"- {f}" for f in facts[:12]) or "(no facts)"
    script_sample = script_text[:800] if script_text else ""

    prompt = (
        f"Extract ALL named real-world people from this true crime story.\n\n"
        f"TOPIC: {topic}\n"
        f"ERA: {era or 'unknown'}\n\n"
        f"RESEARCH FACTS:\n{facts_text}\n\n"
        + (f"SCRIPT EXCERPT:\n{script_sample}\n\n" if script_sample else "")
        + "For each person return:\n"
        "- name: canonical full name\n"
        "- role: one of [suspect, detective, victim, witness, judge, lawyer, family, reporter, investigator]\n"
        "- aliases: list of alternate names/nicknames\n"
        "- gender: male/female/unknown\n"
        "- era: approximate period (e.g. '1980s–1994')\n"
        "- importance: 0.0–1.0 (main subject = 1.0)\n"
        "- context: one-sentence description\n\n"
        "Return ONLY a valid JSON array. Max 8 people. Most important first.\n"
        "Example: [{\"name\": \"Jeffrey Dahmer\", \"role\": \"suspect\", "
        "\"aliases\": [\"Jeff Dahmer\"], \"gender\": \"male\", "
        "\"era\": \"1978–1991\", \"importance\": 1.0, "
        "\"context\": \"Serial killer convicted of 15 murders in Milwaukee\"}]"
    )
    try:
        resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content":
                 "You extract structured data from text. Return only valid JSON arrays."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Extract JSON array from response
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m:
            return []
        entities = json.loads(m.group())
        if not isinstance(entities, list):
            return []
        valid = []
        for e in entities:
            if not isinstance(e, dict) or not e.get("name"):
                continue
            valid.append({
                "name":       str(e.get("name", "")).strip(),
                "role":       str(e.get("role", "suspect")).lower().strip(),
                "aliases":    [str(a) for a in (e.get("aliases") or []) if a],
                "gender":     str(e.get("gender", "unknown")).lower(),
                "era":        str(e.get("era", "") or ""),
                "importance": float(e.get("importance", 0.5)),
                "context":    str(e.get("context", "") or ""),
            })
        return sorted(valid, key=lambda x: x["importance"], reverse=True)
    except Exception as ex:
        print(f"[ENTITY] LLM extraction failed: {ex}")
        return []


def _regex_extract_entities(
    topic: str,
    facts: list[str],
    research: dict,
) -> list[dict]:
    """Fallback: pattern-match proper names from facts, classify by role keywords."""
    main_name = (research.get("real_person") or topic).strip()
    era = (research.get("verified_facts") or {}).get("time_period", "") or ""

    entities: dict[str, dict] = {}

    # Main subject always first
    entities[main_name.lower()] = {
        "name":       main_name,
        "role":       ROLE_SUSPECT,
        "aliases":    [],
        "gender":     "unknown",
        "era":        era,
        "importance": 1.0,
        "context":    f"Main subject of: {topic}",
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
            role = ROLE_WITNESS  # default
            for r, kws in _ROLE_KEYWORDS.items():
                if any(kw in fact_lower for kw in kws):
                    role = r
                    break
            entities[name.lower()] = {
                "name":       name,
                "role":       role,
                "aliases":    [],
                "gender":     "unknown",
                "era":        era,
                "importance": 0.5,
                "context":    fact[:100],
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
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
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

def build_episode_cast(
    research: dict,
    topic: str,
    output_dir: str = _CHARS_DIR,
    script_text: str = "",
    style_preset: str = "default",
    style_keywords: str = "",
) -> dict[str, dict]:
    """
    Full character pipeline:
      1. Load from memory cache (if exists and fresh)
      2. Extract entities from research + script
      3. Fetch real images per entity (multi-stage)
      4. Build AI-reconstruction fallback for any missing images
      5. Assemble cast dict keyed by pipeline role (main/detective/victim/witness)
      6. Save to character memory

    Returns cast dict in the same format as animation_agent.build_cast().
    """
    os.makedirs(output_dir, exist_ok=True)

    # ── Cache check ───────────────────────────────────────────────────────────
    cached = load_character_memory(topic)
    if cached:
        _valid = {k: v for k, v in cached.items()
                  if _validate_portrait_image(v.get("ref_image_path", ""))}
        if len(_valid) >= 1:
            print(f"[CHARACTER] Memory cache hit: {list(_valid.keys())} for '{topic}'")
            return _ensure_cast_completeness(_valid, research, topic, output_dir,
                                             style_preset, style_keywords)

    # ── Entity extraction ─────────────────────────────────────────────────────
    entities = extract_story_entities(research, topic, script_text)

    era  = (research.get("verified_facts") or {}).get("time_period", "") or ""
    locs = (research.get("verified_facts") or {}).get("real_locations", [])
    loc  = locs[0] if locs else ""

    # Assign pipeline roles to entities (main/detective/victim/witness)
    _role_priority = {
        ROLE_SUSPECT:     "main",
        ROLE_DETECTIVE:   "detective",
        ROLE_INVESTIGATOR:"detective",
        ROLE_VICTIM:      "victim",
        ROLE_WITNESS:     "witness",
        ROLE_JUDGE:       "witness",
        ROLE_LAWYER:      "witness",
        ROLE_FAMILY:      "witness",
        ROLE_REPORTER:    "witness",
    }
    cast: dict[str, dict] = {}
    used_roles: set[str] = set()

    for entity in entities:
        pipeline_role = _role_priority.get(entity["role"], "witness")
        # Only first entity per pipeline role
        if pipeline_role in used_roles:
            continue
        used_roles.add(pipeline_role)

        img_path = fetch_real_image(entity, output_dir)

        # AI reconstruction fallback — generate a stable image locked to this entity
        if not img_path:
            img_path = _ai_reconstruction_fallback(entity, output_dir, style_keywords)

        descriptor = _build_entity_descriptor(entity, loc)
        cast[pipeline_role] = {
            "name":           entity["name"],
            "role":           entity["role"],
            "ref_image_path": img_path,
            "descriptor":     descriptor,
            "era":            entity.get("era") or era or "historical documentary",
            "location":       loc or "unknown location",
            "style_preset":   style_preset,
            "style_keywords": style_keywords,
            "image_source":   "real" if img_path else "ai_reconstruction",
            "aliases":        entity.get("aliases", []),
            "gender":         entity.get("gender", "unknown"),
            "context":        entity.get("context", ""),
            "entity_type":    "real_person",
        }

    # Ensure "main" is always present
    cast = _ensure_cast_completeness(cast, research, topic, output_dir,
                                     style_preset, style_keywords)

    # ── Persist to character memory ───────────────────────────────────────────
    save_character_memory(topic, cast)

    _photos = sum(1 for v in cast.values() if v.get("ref_image_path") and
                  os.path.exists(v.get("ref_image_path") or ""))
    print(f"[CHARACTER] Cast assembled: {list(cast.keys())} | "
          f"real photos: {_photos}/{len(cast)}")
    for role, char in cast.items():
        src = char.get("image_source", "?")
        print(f"[CHARACTER] [{role.upper()}] {char['name']} — source={src}")

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
    """
    Persist cast to content/<topic>/characters/cast.json.
    Stores metadata only — image paths are relative to the characters/ dir.
    """
    chars_dir = _characters_dir(topic)
    mem: dict[str, Any] = {}
    for role, char in cast.items():
        img_path = char.get("ref_image_path") or ""
        mem[role] = {
            "name":         char.get("name", ""),
            "role":         char.get("role", ""),
            "aliases":      char.get("aliases", []),
            "gender":       char.get("gender", "unknown"),
            "era":          char.get("era", ""),
            "location":     char.get("location", ""),
            "descriptor":   char.get("descriptor", ""),
            "context":      char.get("context", ""),
            "image_source": char.get("image_source", ""),
            "ref_image_path": img_path,  # absolute path — stays valid in same run env
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
    Load cast from content/<topic>/characters/cast.json if it exists.
    Returns None if not found or invalid.
    Image paths are validated before returning — stale paths are dropped.
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

        # Re-inflate back to full cast dicts
        cast = {}
        for role, data in mem.items():
            img = data.get("ref_image_path", "")
            # Validate stored image still exists and is usable
            if img and not _validate_portrait_image(img):
                img = None
            cast[role] = {**data, "ref_image_path": img,
                          "style_preset":  "default",
                          "style_keywords": "",
                          "entity_type":   "real_person"}
        print(f"[CHARACTER] Memory loaded: {list(cast.keys())} for '{topic}'")
        return cast if cast else None
    except Exception as e:
        print(f"[CHARACTER] Memory load failed: {e}")
        return None
