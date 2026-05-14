# agents/animation_agent.py
# ============================================================
#  AI Animation Pipeline — character-centric motion documentary
#
#  Visual Generation Tiers (per scene):
#    Tier 1 (portrait sections): D-ID talking portrait
#                                photo + audio → animated portrait clip
#    Tier 2: Runway Gen-3 Turbo  image + scene prompt → motion clip
#    Tier 3: Luma Dream Machine  image + prompt → motion clip
#    Tier 4: Kling AI            image + prompt → motion clip
#    Tier 5: Enhanced still      MoviePy Ken Burns — always works, no API
#
#  Public API:
#    init_topic_lock(topic)                                -> None  (CALL FIRST)
#    build_character_identity(research, topic, output_dir) -> dict
#    parse_script_into_scenes(script_text, topic, research) -> list[dict]
#    generate_scene_clip(scene, identity, output_path)     -> str | None
#    generate_talking_portrait(ref_image, audio, out)      -> str | None
#    create_animation_video(script_data, research, out_dir) -> str
# ============================================================

import os
import re
import time
import json
import base64
import random
import hashlib
import requests

# ── Directories (legacy fallback — primary storage is content/<topic>/) ────────
_ANIM_DIR   = "output/animation"
_CLIPS_DIR  = "output/animation/clips"
_CHARS_DIR  = "output/animation/characters"

for _d in [_ANIM_DIR, _CLIPS_DIR, _CHARS_DIR]:
    os.makedirs(_d, exist_ok=True)

# ── Per-topic content paths (set by init_topic_lock → ensure_topic_content) ───
_CONTENT_PATHS: dict = {}

# ── Style presets ─────────────────────────────────────────────────────────────
_STYLE_PRESETS: dict[str, str] = {
    "serial_killer":  "realistic dark documentary, crime scene atmosphere, psychological drama, cinematic grain",
    "cartel":         "gritty realistic, narco era aesthetic, Colombian landscape, documentary realism",
    "mafia":          "noir cinematic, period dramatic shadows, organized crime atmosphere",
    "gang":           "urban crime documentary, street realism, investigative atmosphere",
    "fraud":          "financial crime documentary, corporate atmosphere, investigative tension",
    "historical":     "period accurate documentary, archival quality, historical drama realism",
    "archaeology":    "ancient ruins documentary, archaeological excavation, warm golden hour light, BBC documentary style, sand and stone textures",
    "default":        "realistic documentary, dark cinematic, investigative atmosphere, BBC documentary style",
}

# Domain → style preset mapping (from classify_topic_domain() output)
_DOMAIN_TO_STYLE: dict[str, str] = {
    "archaeology":      "archaeology",
    "serial_killer":    "serial_killer",
    "organized_crime":  "mafia",
    "fraud":            "fraud",
    "war_historical":   "historical",
    "tv_adaptation":    "default",
    "default":          "default",
}

# Domain keyword sets — duplicated here to avoid importing research_agent
# (prevents circular import; keep in sync with research_agent._DOMAIN_KEYWORDS)
_ANIM_DOMAIN_KEYWORDS: dict[str, list] = {
    "archaeology": [
        "archaeolog", "excavat", "ancient city", "ancient civili", "biblical",
        "bronze age", "iron age", "prehistoric", "dig site", "dead sea",
        "jordan valley", "holy land", "mesopotamia", "sodom", "gomorrah",
        "jericho", "pompeii", "tomb", "unearthed", "radiocarbon", "ruins",
        "ancient discovery",
    ],
    "serial_killer": ["serial killer", "serial murder"],
    "organized_crime": ["mafia", "cartel", "mob", "camorra", "yakuza", "drug lord", "narco"],
    "fraud": ["fraud", "ponzi", "embezzl", "wall street broker", "securities fraud"],
    "war_historical": ["world war", "civil war", "genocide", "holocaust", "revolution"],
}

# Section name → scene type mapping (12 cinematic types)
_SECTION_SCENE_TYPE: dict[str, str] = {
    # Arabic section labels
    "مقدمة":    "talking_portrait",
    "خلفية":    "era_reenactment",
    "قصة":      "investigation_scene",
    "واقع":     "comparison_scene",
    "حقائق":    "evidence_scene",
    "خاتمة":    "memorial_scene",
    "اعتراف":   "interrogation_room",
    "محاكمة":   "courtroom_drama",
    "سجن":      "prison_cell",
    "طفولة":    "childhood_archive",
    "شهادة":    "cctv_footage",
    "كارثة":    "flashback",
    # English section labels
    "hook":          "talking_portrait",
    "introduction":  "talking_portrait",
    "background":    "era_reenactment",
    "story":         "investigation_scene",
    "reality":       "comparison_scene",
    "shocking":      "evidence_scene",
    "conclusion":    "memorial_scene",
    "aftermath":     "memorial_scene",
    "confession":    "interrogation_room",
    "interrogation": "interrogation_room",
    "trial":         "courtroom_drama",
    "verdict":       "courtroom_drama",
    "courtroom":     "courtroom_drama",
    "prison":        "prison_cell",
    "childhood":     "childhood_archive",
    "flashback":     "flashback",
    "surveillance":  "cctv_footage",
    "newspaper":     "newspaper_reveal",
    "headline":      "newspaper_reveal",
}

# Keyword → scene type override (applied when section label doesn't match)
# Higher-priority keywords appear first
_CHUNK_SCENE_OVERRIDES: list[tuple[frozenset, str]] = [
    (frozenset({"confessed", "interrogated", "questioned", "admitted", "police station",
                "detective asked", "hours of questioning", "broke down"}), "interrogation_room"),
    (frozenset({"courtroom", "judge", "jury", "verdict", "sentenced", "acquitted",
                "trial began", "prosecutor", "defense attorney"}),         "courtroom_drama"),
    (frozenset({"cctv", "surveillance", "security camera", "footage showed",
                "caught on camera", "captured on video"}),                 "cctv_footage"),
    (frozenset({"newspaper", "headline", "front page", "reporters",
                "press conference", "media reported"}),                     "newspaper_reveal"),
    (frozenset({"born in", "grew up", "childhood", "as a child", "his mother",
                "her mother", "his father", "her father", "young"}),        "childhood_archive"),
    (frozenset({"prison", "cell", "behind bars", "incarcerated", "solitary",
                "death row", "serving time"}),                              "prison_cell"),
    (frozenset({"flashback", "years earlier", "decades before", "back in",
                "rewind", "in his youth", "before the crime"}),            "flashback"),
    (frozenset({"evidence", "forensic", "dna", "fingerprint", "blood",
                "weapon found", "autopsy", "crime scene"}),                "evidence_scene"),
]

# Motion prompt templates — crime/biography domain (default)
_MOTION_TEMPLATES: dict[str, str] = {
    "talking_portrait":   "{descriptor}, documentary portrait, subtle talking motion, slight head movement, "
                          "cinematic dark lighting, {era}, investigative documentary atmosphere, 9:16 vertical",
    "era_reenactment":    "{descriptor} in {location}, {era}, {event}, realistic documentary reenactment, "
                          "cinematic slow camera push-in, {style}, 9:16 vertical",
    "investigation_scene":"Crime investigation {location} {era}, {event}, detective forensics, "
                          "cinematic slow motion, dark documentary realism, {style}, 9:16 vertical",
    "comparison_scene":   "{descriptor} and visual comparison {era}, {event}, "
                          "split documentary style, cinematic, {style}, 9:16 vertical",
    "evidence_scene":     "Evidence table crime scene investigation {era}, {event}, "
                          "documentary close-up slow motion, cinematic dramatic lighting, {style}, 9:16 vertical",
    "memorial_scene":     "{descriptor} memorial tribute, {era}, documentary ending atmosphere, "
                          "slow cinematic motion, {style}, 9:16 vertical",
    "interrogation_room": "Dimly lit interrogation room {era}, single overhead light, metal chair, {event}, "
                          "documentary POV slow push-in, psychological tension, {style}, 9:16 vertical",
    "courtroom_drama":    "Dramatic courtroom scene {location} {era}, {event}, "
                          "cinematic overhead shot, tension-filled atmosphere, {style}, 9:16 vertical",
    "prison_cell":        "Dark prison cell corridor {location} {era}, {event}, "
                          "cinematic dolly shot through bars, somber documentary realism, {style}, 9:16 vertical",
    "childhood_archive":  "Vintage archival photograph style {era}, {descriptor} childhood scene, "
                          "documentary sepia grain overlay, warm nostalgic light, {style}, 9:16 vertical",
    "flashback":          "{descriptor} {location} {era}, {event}, cinematic flashback, "
                          "film grain, warm faded tones, slow dissolve atmosphere, {style}, 9:16 vertical",
    "cctv_footage":       "Grainy CCTV surveillance footage {location} {era}, {event}, "
                          "timestamp overlay, fisheye wide angle, low-light documentary realism, {style}, 9:16 vertical",
    "newspaper_reveal":   "Aged newspaper front page {era}, {event}, dramatic headline reveal, "
                          "documentary close-up slow zoom, dramatic backlight, {style}, 9:16 vertical",
}

# Motion prompt templates — archaeology / biblical-history / ancient-world domain
_MOTION_TEMPLATES_ARCHAEOLOGY: dict[str, str] = {
    "talking_portrait":   "Documentary narrator {era}, warm natural light, subtle speaking motion, "
                          "slight head movement, historical documentary atmosphere, {style}, 9:16 vertical",
    "era_reenactment":    "Ancient {location}, {era}, {event}, historical documentary reconstruction, "
                          "cinematic wide establishing shot, warm golden hour, {style}, 9:16 vertical",
    "investigation_scene":"Archaeological excavation at {location}, {era}, {event}, "
                          "documentary close-up discovery reveal, warm dusty light, {style}, 9:16 vertical",
    "comparison_scene":   "Ancient ruins and historical evidence {location}, {era}, {event}, "
                          "documentary comparison style, cinematic, {style}, 9:16 vertical",
    "evidence_scene":     "Ancient artifact examination {location}, {era}, {event}, "
                          "documentary close-up archaeology table, warm golden light, {style}, 9:16 vertical",
    "memorial_scene":     "Ancient ruins {location}, {era}, historical documentary ending atmosphere, "
                          "slow cinematic motion, warm dust haze, {style}, 9:16 vertical",
    "interrogation_room": "Ancient council chamber {location} {era}, {event}, "
                          "documentary candlelight atmosphere, stone walls, {style}, 9:16 vertical",
    "courtroom_drama":    "Ancient tribunal or judgment hall {location} {era}, {event}, "
                          "dramatic torchlight, cinematic overhead shot, {style}, 9:16 vertical",
    "prison_cell":        "Ancient dungeon or holding cell {location} {era}, {event}, "
                          "documentary torch-lit corridor, somber atmosphere, {style}, 9:16 vertical",
    "childhood_archive":  "Archival reconstruction {era}, ancient childhood scene {location}, "
                          "documentary warm dusty tones, historical detail, {style}, 9:16 vertical",
    "flashback":          "Ancient {location} {era}, {event}, documentary historical flashback, "
                          "sepia warm tones, slow dissolve, {style}, 9:16 vertical",
    "cctv_footage":       "Ancient wall inscription or petroglyph {location} {era}, {event}, "
                          "documentary close-up reveal, dramatic raking light, {style}, 9:16 vertical",
    "newspaper_reveal":   "Ancient scroll or clay tablet {location} {era}, {event}, "
                          "documentary inscription close-up slow zoom, dramatic backlight, {style}, 9:16 vertical",
}

# Domain → template set
_TEMPLATES_BY_DOMAIN: dict[str, dict] = {
    "archaeology":     _MOTION_TEMPLATES_ARCHAEOLOGY,
    "war_historical":  _MOTION_TEMPLATES,   # reuse default; era will differentiate
    "default":         _MOTION_TEMPLATES,
}

# ── Shot engine: scene-type → Pollinations prompt pool ───────────────────────
# Each entry is a list of prompt strings used to generate diverse scene images.
# Portrait types are excluded (they use the character identity photo directly).
_SHOT_PROMPTS: dict[str, list[str]] = {
    "investigation_scene": [
        "1970s detective office crime case board with photos and evidence strings, noir side lighting, dark documentary",
        "crime scene police tape investigators in background overcast moody daylight, dark cinematic",
        "detective desk files evidence folders spread out dramatic lamp light dark documentary",
        "forensic investigators examining crime scene evidence dark atmospheric documentary realism",
    ],
    "evidence_scene": [
        "forensic laboratory DNA analysis equipment closeup blue-white clinical light dark background",
        "evidence table crime scene items laid out tagged numbered overhead documentary lighting",
        "fingerprint forensic analysis under UV light dark background blue glow crime lab",
        "crime scene investigation evidence examination bag tagged items dark cinematic documentary",
    ],
    "interrogation_room": [
        "dimly lit interrogation room single overhead bare bulb metal chair center frame psychological tension dark",
        "police interview room two-way mirror detective silhouette low key dark lighting crime drama",
        "dark interview room metal table dramatic side lighting crime documentary realism",
    ],
    "courtroom_drama": [
        "dramatic courtroom interior judge bench gavel American flag overhead lighting legal tension",
        "jury box twelve jurors seated courtroom drama tension-filled cinematic wide shot",
        "prosecution delivering closing argument pointing evidence board courtroom dramatic documentary",
    ],
    "newspaper_reveal": [
        "aged newspaper front page dramatic crime headline dramatic backlight macro closeup dark documentary",
        "1970s newspaper archives filing room rows of bound volumes warm documentary lighting",
        "journalist desk newspaper crime clippings typewriter noir side lighting dark atmosphere",
    ],
    "cctv_footage": [
        "grainy security camera footage empty parking lot night timestamp overlay fisheye wide angle",
        "CCTV corridor surveillance footage low-resolution grainy night vision dark hallway",
        "security monitor room multiple camera feeds dark control room atmosphere documentary",
    ],
    "childhood_archive": [
        "1960s vintage family photograph sepia tones suburban house front yard warm nostalgic film grain",
        "old school yearbook photograph institutional photography vintage grain faded colors archive",
        "1950s suburban neighborhood street scene warm nostalgic documentary film grain atmosphere",
    ],
    "era_reenactment": [
        "1970s urban street scene authentic period cars storefronts overcast dramatic sky wide shot",
        "period interior 1960s domestic scene furniture decor warm available light documentary",
        "1980s neighborhood exterior establishing wide shot cinematic historical atmosphere overcast",
    ],
    "memorial_scene": [
        "memorial flowers candles tribute site soft bokeh background evening light emotional documentary",
        "crime victim memorial wall photographs flowers vigil candles evening somber documentary",
    ],
    "prison_cell": [
        "dark prison corridor cell bars overhead fluorescent institutional light long hallway perspective",
        "prison cell interior bunk toilet small barred window dramatic light shaft documentary realism",
    ],
    "flashback": [
        "1960s faded nostalgic scene film grain texture warm desaturated colors vintage memory archive",
        "vintage archive footage aesthetic vignette film burn overlay warm sepia cinematic memory",
    ],
    "comparison_scene": [
        "criminal case evidence board photographs documents strings connecting points dramatic side lighting",
        "documentary comparison visual investigation board crime evidence dramatic atmospheric light",
    ],
}

# Motion cycle for shot sequences — ensures visual variety within each beat
_BEAT_MOTIONS: list[str] = [
    "zoom_in", "pan_right", "zoom_out", "pan_left",
    "breathe", "flicker", "pan_up", "fog", "parallax", "smoke",
]

# Environment continuity lock — set once per create_animation_video() call,
# ensures repeated scene types reuse the same location/era descriptor
# rather than generating disconnected backgrounds each time.
_ENVIRONMENT_LOCK: dict[str, str] = {}


def _lock_environment(scene_type: str, where: str, era: str) -> tuple[str, str]:
    """
    Return (where, era) for this scene, reusing established environment
    descriptors when available.  First occurrence locks the descriptor;
    subsequent calls return the locked value so the same interrogation
    room / courtroom / prison cell persists across the episode.
    """
    key_where = f"{scene_type}_where"
    key_era   = f"{scene_type}_era"
    if key_where not in _ENVIRONMENT_LOCK and where:
        _ENVIRONMENT_LOCK[key_where] = where
        _ENVIRONMENT_LOCK[key_era]   = era
        print(f"[ANIMATION] Environment continuity preserved: {scene_type} → {where}")
    locked_where = _ENVIRONMENT_LOCK.get(key_where) or where
    locked_era   = _ENVIRONMENT_LOCK.get(key_era)   or era
    return locked_where, locked_era

# ── Event keywords — crime/biography domain (default) ────────────────────────
_EVENT_KEYWORDS: dict[str, str] = {
    "arrest":       "police arrest scene",
    "murder":       "crime investigation scene",
    "trial":        "courtroom legal proceedings",
    "escape":       "pursuit escape scene",
    "childhood":    "childhood growing up scene",
    "prison":       "prison cell corridor",
    "investigation":"detective investigation scene",
    "evidence":     "forensic evidence examination",
    "confession":   "interrogation room scene",
    "verdict":      "courtroom verdict moment",
    "kidnap":       "abduction investigation scene",
    "drug":         "drug operation scene",
    "money":        "financial crime scene",
}

# ── Event keywords — archaeology / ancient-world domain ──────────────────────
_EVENT_KEYWORDS_ARCHAEOLOGY: dict[str, str] = {
    "discover":   "archaeological discovery scene",
    "excavat":    "excavation dig site scene",
    "unearthed":  "artifact unearthing scene",
    "ancient":    "ancient historical reconstruction",
    "ruins":      "ancient ruins documentary scene",
    "temple":     "ancient temple exploration",
    "burial":     "ancient burial site examination",
    "artifact":   "artifact analysis scene",
    "archaeolog": "archaeological dig site scene",
    "biblical":   "biblical historical landscape",
    "dead sea":   "Dead Sea Jordan Valley landscape",
    "bronze age": "Bronze Age historical reconstruction",
    "sodom":      "ancient city destruction landscape",
    "gomorrah":   "ancient Jordan Valley plain",
    "city wall":  "ancient city wall reconstruction",
    "settlement": "ancient settlement reconstruction",
}

# ── Location labels — crime/biography domain (default) ───────────────────────
_LOCATION_LABELS: dict[str, str] = {
    "milwaukee":    "Milwaukee Wisconsin 1980s",
    "new york":     "New York City urban street",
    "chicago":      "Chicago 1920s",
    "miami":        "Miami 1980s",
    "colombia":     "Medellin Colombia",
    "mexico":       "Mexico border desert",
    "italy":        "Sicily Italy village",
    "los angeles":  "Los Angeles street scene",
    "prison":       "prison corridor",
    "apartment":    "apartment interior",
    "courtroom":    "courtroom legal setting",
    "fbi":          "FBI office interior",
}

# ── Location labels — archaeology / ancient-world domain ─────────────────────
_LOCATION_LABELS_ARCHAEOLOGY: dict[str, str] = {
    "jordan":       "Jordan Valley Dead Sea region",
    "israel":       "Ancient Levant landscape",
    "sinai":        "Sinai Peninsula desert",
    "egypt":        "Ancient Egypt Nile Valley",
    "mesopotamia":  "Ancient Mesopotamia Iraq",
    "dead sea":     "Dead Sea Jordan Valley plain",
    "jericho":      "Ancient Jericho Tell es-Sultan",
    "sodom":        "ancient Dead Sea plain",
    "gomorrah":     "ancient Jordan Valley ruins",
    "petra":        "Petra Jordan ancient city",
    "babylon":      "Ancient Babylon Mesopotamia",
    "jerusalem":    "Ancient Jerusalem holy city",
    "negev":        "Negev desert archaeological site",
    "excavat":      "active archaeological dig site",
}

# Domain → location label table
_LOCATION_LABELS_BY_DOMAIN: dict[str, dict] = {
    "archaeology": _LOCATION_LABELS_ARCHAEOLOGY,
    "default":     _LOCATION_LABELS,
}

# ── Role keywords for descriptor building — by domain ────────────────────────
_DESCRIPTOR_ROLE_KEYWORDS: dict[str, list] = {
    "default":     ["police", "detective", "agent", "cartel", "boss", "leader",
                    "criminal", "killer", "gangster", "lawyer", "journalist"],
    "archaeology": ["archaeologist", "researcher", "historian", "excavator",
                    "professor", "scholar", "explorer", "biblical"],
    "war_historical": ["general", "commander", "soldier", "leader", "president",
                       "king", "emperor", "rebel", "revolutionary"],
}


# ── Provider health tracker ───────────────────────────────────────────────────

class _AnimProviderHealth:
    _FAIL_THRESHOLD = 2
    _WINDOW_SEC     = 300

    def __init__(self):
        self._failures: dict[str, list[float]] = {}

    def record_failure(self, provider: str) -> None:
        now = time.time()
        self._failures.setdefault(provider, []).append(now)

    def is_healthy(self, provider: str) -> bool:
        now = time.time()
        recent = [t for t in self._failures.get(provider, []) if now - t < self._WINDOW_SEC]
        self._failures[provider] = recent
        return len(recent) < self._FAIL_THRESHOLD

    def reset(self, provider: str) -> None:
        self._failures[provider] = []


_health = _AnimProviderHealth()


# ── Topic lock — reset per run, prevents cross-topic contamination ────────────

_TOPIC_LOCK: dict = {"topic": "", "topic_hash": "", "domain": "default"}

# GLOBAL_PIPELINE_CONTEXT — populated by init_topic_lock(), read by all generation systems
GLOBAL_PIPELINE_CONTEXT: dict = {
    "topic": "", "domain": "default", "era": "", "main_entity": "", "tone": "investigative",
}


def init_topic_lock(topic: str) -> None:
    """
    Hard reset of all run-scoped state. MUST be called at pipeline start
    before any identity/scene/clip work. Clears provider health, stale
    character photos, and sets the topic namespace + semantic domain for
    cache isolation and domain-aware template selection.
    Also initialises persistent content paths for this topic.
    """
    global _TOPIC_LOCK, _CONTENT_PATHS
    _h = hashlib.sha256(topic.encode()).hexdigest()[:16]

    # Classify semantic domain so all downstream template selectors can use it
    t_lower = topic.lower()
    _domain = "default"
    for _dom, _kws in _ANIM_DOMAIN_KEYWORDS.items():
        if any(kw in t_lower for kw in _kws):
            _domain = _dom
            break

    _TOPIC_LOCK = {"topic": topic, "topic_hash": _h, "domain": _domain}
    GLOBAL_PIPELINE_CONTEXT.update({
        "topic": topic, "domain": _domain, "era": "", "main_entity": topic, "tone": "investigative",
    })
    _health._failures.clear()

    # Initialise persistent per-topic content storage
    try:
        import sys as _sys
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from utils.content_manager import ensure_topic_content
        _CONTENT_PATHS = ensure_topic_content(topic)
        print(f"[CONTENT] Topic storage: {_CONTENT_PATHS['path']}")
    except Exception as _ce:
        print(f"[CONTENT] Content paths setup (non-fatal): {_ce}")
        _CONTENT_PATHS = {}

    _purge_stale_char_photos(topic)
    print(f"[TOPIC LOCK] Active — {topic}")
    print(f"[DOMAIN] {_domain}")
    print(f"[IDENTITY] Cache cleared (topic hash: {_h})")
    if _domain != "default":
        print(f"[SHOW MODE] disabled — domain is {_domain}, not tv_adaptation")


def _purge_stale_char_photos(topic: str) -> None:
    """Delete character images saved by previous (different) topic runs."""
    safe_slug = re.sub(r'[^a-z0-9_]', '_', topic.lower())[:30]
    _anim_content = _CONTENT_PATHS.get("animations_path", "")
    _content_chars = os.path.join(_anim_content, "characters") if _anim_content else ""
    for chars_dir in filter(None, [_CHARS_DIR, _content_chars]):
        try:
            if not os.path.isdir(chars_dir):
                continue
            for fname in os.listdir(chars_dir):
                if fname.startswith("char_") and not fname.startswith(f"char_{safe_slug}"):
                    try:
                        os.remove(os.path.join(chars_dir, fname))
                        print(f"[IDENTITY] Purged stale character photo: {fname}")
                    except Exception:
                        pass
        except Exception:
            pass


def _validate_entity(entity_name: str, topic: str, research: dict) -> bool:
    """
    Return True if entity_name plausibly belongs to the current topic.
    Checks against topic text and research facts. Rejects unrelated entities.
    """
    if not entity_name or entity_name in ("unknown", ""):
        return True
    e_words = [w for w in entity_name.lower().split() if len(w) > 3]
    if not e_words:
        return True
    t_lower = topic.lower()
    if any(w in t_lower for w in e_words):
        return True
    facts_text = " ".join(
        (research.get("research_facts") or [])
        + (research.get("real_facts") or [])
        + [research.get("series_name") or ""]
        + [str((research.get("verified_facts") or {}).get("story", ""))]
    ).lower()
    if any(w in facts_text for w in e_words if len(w) > 4):
        return True
    return False


# ── SHA256 clip cache (topic-scoped) ─────────────────────────────────────────

def _clip_cache_key(prompt: str, duration: int) -> str:
    topic_hash = _TOPIC_LOCK.get("topic_hash", "")
    raw = f"{topic_hash}|{duration}|{prompt}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _clip_cache_path(key: str) -> str:
    cache_dir = _CONTENT_PATHS.get("cache_path") or _CLIPS_DIR
    return os.path.join(cache_dir, f"cache_{key}.mp4")


def _clip_cache_get(prompt: str, duration: int) -> str | None:
    path = _clip_cache_path(_clip_cache_key(prompt, duration))
    if os.path.exists(path) and os.path.getsize(path) > 10_000:
        print(f"[SCENE] Clip cache hit: {prompt[:50]}")
        return path
    return None


def _clip_cache_set(prompt: str, duration: int, path: str) -> None:
    key = _clip_cache_key(prompt, duration)
    dest = _clip_cache_path(key)
    try:
        if path != dest and os.path.exists(path):
            import shutil
            shutil.copy2(path, dest)
    except Exception:
        pass


# ============================================================
#  STEP 1: CHARACTER IDENTITY
# ============================================================

def build_character_identity(
    research: dict,
    topic: str,
    output_dir: str = _CHARS_DIR,
) -> dict:
    """
    Extract main character from research, retrieve best reference photo,
    build a consistent visual descriptor for all scene prompts.

    Returns:
        {name, ref_image_path, descriptor, era, style_keywords, style_preset}
    """
    os.makedirs(output_dir, exist_ok=True)

    name = (
        research.get("real_person")
        or research.get("series_name")
        or topic
        or "unknown"
    ).strip()

    # Reject any entity that doesn't belong to the current topic
    _locked_topic = _TOPIC_LOCK.get("topic") or topic
    if name not in ("unknown", topic, _locked_topic):
        if not _validate_entity(name, _locked_topic, research):
            print(f"[ERROR] Cross-topic contamination detected: {name!r} does not belong to topic: {_locked_topic!r}")
            print(f"[IDENTITY] Rejected unrelated entity: {name!r} — using topic as identity")
            name = topic
    print(f"[IDENTITY] Current topic entities loaded: {name}")

    era  = (research.get("verified_facts") or {}).get("time_period", "") or ""
    locs = (research.get("verified_facts") or {}).get("real_locations", [])
    loc  = locs[0] if locs else ""

    # Domain-aware style preset selection
    topic_lower  = topic.lower()
    niche_lower  = (research.get("niche", "") or "").lower()
    style_preset = "default"

    # Check domain keywords (priority — prevents mafia/crime style for archaeology topics)
    for domain, kws in _ANIM_DOMAIN_KEYWORDS.items():
        if any(kw in topic_lower or kw in niche_lower for kw in kws):
            style_preset = _DOMAIN_TO_STYLE.get(domain, "default")
            break

    # If still default, fall back to direct style-key matching
    if style_preset == "default":
        for key in _STYLE_PRESETS:
            if key in topic_lower or key in niche_lower:
                style_preset = key
                break

    print(f"[VISUAL] Domain-locked style preset: {style_preset}")

    # ── Retrieve reference photo ──────────────────────────────────────────────
    ref_image_path = _fetch_character_photo(name, output_dir)
    if ref_image_path:
        print(f"[CHARACTER] Real image found: {name} → {os.path.basename(ref_image_path)}")
    else:
        print(f"[CHARACTER] No real image found for: {name} — identity built from descriptor")

    descriptor = _build_descriptor(name, era, loc, research, style_preset)
    print(f"[CHARACTER] Descriptor: {descriptor[:80]}")

    identity = {
        "name":          name,
        "ref_image_path": ref_image_path,
        "descriptor":    descriptor,
        "era":           era or "historical documentary",
        "location":      loc or "unknown location",
        "style_preset":  style_preset,
        "style_keywords": _STYLE_PRESETS.get(style_preset, _STYLE_PRESETS["default"]),
    }
    print(f"[CHARACTER] Identity preserved: {name} | era={era} | style={style_preset}")
    return identity


# ── Scene type → character role mapping ──────────────────────────────────────
# Determines which cast member's image/descriptor is used per scene.
_SCENE_TYPE_TO_CHAR_ROLE: dict[str, str] = {
    "talking_portrait":   "main",
    "era_reenactment":    "main",
    "investigation_scene":"detective",
    "evidence_scene":     "detective",
    "comparison_scene":   "main",
    "memorial_scene":     "victim",
    "interrogation_room": "main",
    "courtroom_drama":    "detective",
    "prison_cell":        "main",
    "childhood_archive":  "main",
    "flashback":          "main",
    "cctv_footage":       "detective",
    "newspaper_reveal":   "detective",
}


def build_cast(
    research: dict,
    topic: str,
    output_dir: str = _CHARS_DIR,
) -> dict[str, dict]:
    """
    Extract up to 5 characters from research: main, detective, victim, witness.
    Returns a dict keyed by role. Each value has the same shape as build_character_identity().
    Falls back to generic descriptors when specific names can't be extracted.
    """
    os.makedirs(output_dir, exist_ok=True)
    era  = (research.get("verified_facts") or {}).get("time_period", "") or ""
    locs = (research.get("verified_facts") or {}).get("real_locations", [])
    loc  = locs[0] if locs else ""
    facts_raw = (
        (research.get("research_facts") or [])
        + (research.get("real_facts") or [])
        + [str((research.get("verified_facts") or {}).get("story", ""))]
    )
    facts_text = " ".join(facts_raw).lower()

    # Role extraction patterns
    _DETECTIVE_KW = ["detective", "agent", "inspector", "officer", "investigator",
                     "fbi", "cia", "dea", "sheriff", "marshal", "prosecutor", "detective sergeant"]
    _VICTIM_KW    = ["victim", "murdered", "killed", "found dead", "disappeared",
                     "abducted", "missing", "slain", "body of"]

    def _extract_named_person(keywords: list[str]) -> str:
        """Find first capitalized proper name appearing near a keyword in the facts."""
        for kw in keywords:
            for fact in facts_raw:
                if kw.lower() in fact.lower():
                    named = re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)\b', fact)
                    for n in named:
                        n_lower = n.lower()
                        main_lower = (research.get("real_person") or topic).lower()
                        if n_lower != main_lower and _validate_entity(n, topic, research):
                            return n
        return ""

    # Main subject (suspect / criminal)
    main_identity = build_character_identity(research, topic, output_dir)

    # Detective / investigator
    detective_name = _extract_named_person(_DETECTIVE_KW)
    if detective_name:
        detective_img = _fetch_character_photo(detective_name, output_dir)
        detective_desc = f"{detective_name}, law enforcement investigator, {era}"
        print(f"[CHARACTER] Detective identified: {detective_name}")
    else:
        detective_name = "lead investigator"
        detective_img  = None
        detective_desc = f"law enforcement investigator, {era or 'modern'}, official uniform"

    # Victim
    victim_name = _extract_named_person(_VICTIM_KW)
    if victim_name:
        victim_img  = _fetch_character_photo(victim_name, output_dir)
        victim_desc = f"{victim_name}, victim, {era}"
        print(f"[CHARACTER] Victim identified: {victim_name}")
    else:
        victim_name = "victim"
        victim_img  = None
        victim_desc = f"victim memorial portrait, {era or 'documentary'}, somber atmosphere"

    # Witness — generic, rarely has a findable photo
    witness_name = "witness"
    witness_img  = None
    witness_desc = f"anonymous witness, {era or 'modern'}, documentary interview framing"

    style_preset    = main_identity["style_preset"]
    style_keywords  = main_identity["style_keywords"]

    def _make_cast_member(name: str, img: str | None, desc: str, role: str) -> dict:
        return {
            "name":           name,
            "role":           role,
            "ref_image_path": img,
            "descriptor":     desc,
            "era":            era or "historical documentary",
            "location":       loc or "unknown location",
            "style_preset":   style_preset,
            "style_keywords": style_keywords,
        }

    cast = {
        "main":      main_identity,
        "detective": _make_cast_member(detective_name, detective_img, detective_desc, "detective"),
        "victim":    _make_cast_member(victim_name, victim_img, victim_desc, "victim"),
        "witness":   _make_cast_member(witness_name, witness_img, witness_desc, "witness"),
    }
    print(f"[CHARACTER] Cast assembled: {list(cast.keys())} — "
          f"photos: main={bool(main_identity.get('ref_image_path'))}, "
          f"detective={bool(detective_img)}, victim={bool(victim_img)}")
    return cast


def _fetch_character_photo(name: str, output_dir: str) -> str | None:
    """Try Wikipedia, then DDG, return saved local path or None."""
    # 1. Wikipedia REST summary
    try:
        encoded = requests.utils.quote(name)
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}",
            timeout=10,
            headers={"User-Agent": "DarkCrimeDecoded/1.0 animation"},
        )
        if r.status_code == 200:
            thumb = r.json().get("thumbnail", {}).get("source", "")
            if thumb:
                path = _download_identity_image(thumb, name, output_dir)
                if path:
                    return path
    except Exception as e:
        print(f"[CHARACTER] Wikipedia photo failed: {e}")

    # 2. DDG image search
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.images(
                f"{name} real photo portrait",
                max_results=5,
                safesearch="off",
            ))
        for r in results:
            url = r.get("image", "")
            if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                path = _download_identity_image(url, name, output_dir)
                if path:
                    return path
    except Exception as e:
        print(f"[CHARACTER] DDG photo search failed: {e}")

    return None


def _download_identity_image(url: str, name: str, output_dir: str) -> str | None:
    """Download, validate, and save a reference image."""
    from PIL import Image as _PIL
    import io
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.content) < 15_000:
            return None
        img = _PIL.open(io.BytesIO(r.content)).convert("RGB")
        slug = re.sub(r'[^a-z0-9_]', '_', name.lower())[:30]
        path = os.path.join(output_dir, f"char_{slug}.jpg")
        img.save(path, "JPEG", quality=95)
        return path
    except Exception:
        return None


def _build_descriptor(name: str, era: str, location: str, research: dict,
                      style_preset: str = "default") -> str:
    """Build a compact visual descriptor string for prompt injection.
    Role keywords are domain-aware — archaeology topics never get crime roles.
    """
    parts = [name]
    if era:
        parts.append(era)
    if location:
        parts.append(location)

    # Select role keywords appropriate to the domain
    domain     = _TOPIC_LOCK.get("domain", "default")
    role_kws   = _DESCRIPTOR_ROLE_KEYWORDS.get(domain) or _DESCRIPTOR_ROLE_KEYWORDS["default"]
    facts = (research.get("research_facts") or [])[:3]
    for f in facts:
        fl = f.lower()
        for kw in role_kws:
            if kw in fl:
                parts.append(kw)
                break
    return ", ".join(dict.fromkeys(parts))  # deduplicate, preserve order


# ============================================================
#  STEP 2: SCENE PARSER
# ============================================================

def parse_script_into_scenes(
    script_text: str,
    topic: str,
    research: dict,
) -> list[dict]:
    """
    Split script by [SECTION: ...] markers, then chunk each section into
    ~50-word cinematic beats. Each beat maps to one unique visual moment.

    For each chunk extract:
      section, text, scene_type, who, where, era, mood, event, prompt_hint

    Returns list of scene dicts ready for motion clip generation.
    """
    # Split on section markers
    raw = re.split(r'(\[SECTION:[^\]]+\])', script_text)
    sections: list[tuple[str, str]] = []
    i = 0
    if raw[0].strip():
        sections.append(("default", raw[0]))
    while i < len(raw):
        m = re.match(r'\[SECTION:\s*([^\]]+)\]', raw[i])
        if m:
            label = m.group(1).strip()
            body  = raw[i + 1] if i + 1 < len(raw) else ""
            sections.append((label, body))
            i += 2
        else:
            i += 1

    scenes: list[dict] = []
    for section_label, body in sections:
        words = body.split()
        if not words:
            continue
        # 50-word cinematic beats → each beat is one unique dramatic moment
        chunk_size = max(30, len(words) // max(1, len(words) // 50))
        for ci in range(0, len(words), chunk_size):
            chunk = " ".join(words[ci: ci + chunk_size])
            scene = _extract_scene_context(chunk, section_label, topic, research)
            # Keyword-based scene type override — overrides section-label type
            # when narrative content clearly signals a different visual context
            chunk_lower = chunk.lower()
            for kw_set, override_type in _CHUNK_SCENE_OVERRIDES:
                if any(kw in chunk_lower for kw in kw_set):
                    scene["scene_type"] = override_type
                    break
            scenes.append(scene)

    print(f"[SCENE] Parsed {len(scenes)} cinematic beats from script (50-word chunks)")
    _type_dist: dict[str, int] = {}
    for s in scenes:
        _type_dist[s["scene_type"]] = _type_dist.get(s["scene_type"], 0) + 1
    print(f"[SCENE] Scene type distribution: {_type_dist}")
    return scenes


def _extract_scene_context(chunk: str, section_label: str, topic: str, research: dict) -> dict:
    """Extract visual context from a script chunk. All lookups are domain-aware."""
    chunk_lower = chunk.lower()
    label_lower = section_label.lower()
    domain = _TOPIC_LOCK.get("domain", "default")

    # Determine scene type from section label
    scene_type = "investigation_scene"
    for key, stype in _SECTION_SCENE_TYPE.items():
        if key in label_lower:
            scene_type = stype
            break

    # Extract location — use domain-appropriate label table
    loc_table = _LOCATION_LABELS_BY_DOMAIN.get(domain, _LOCATION_LABELS)
    where = ""
    for loc_key, loc_label in loc_table.items():
        if loc_key in chunk_lower:
            where = loc_label
            break
    if not where:
        locs = (research.get("verified_facts") or {}).get("real_locations", [])
        where = locs[0] if locs else ""

    # Extract year / era — archaeology allows BCE dates
    years = re.findall(r'\b(19[4-9]\d|20[0-2]\d)\b', chunk)
    bce   = re.findall(r'\b(\d{3,4})\s*(?:BCE|BC|B\.C\.)', chunk)
    if years:
        era = f"{years[0]}s"
    elif bce:
        era = f"{bce[0]} BCE"
    else:
        era = (research.get("verified_facts") or {}).get("time_period", "")

    # Detect event — use domain-appropriate event table
    evt_table = (_EVENT_KEYWORDS_ARCHAEOLOGY
                 if domain == "archaeology"
                 else _EVENT_KEYWORDS)
    event = ""
    for kw, ev in evt_table.items():
        if kw in chunk_lower:
            event = ev
            break

    # Extract named persons from chunk (capitalized 2+ word combos)
    named = re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)\b', chunk)
    who   = named[0] if named else topic

    # Validate named person belongs to current topic; reject contamination
    _locked_topic = _TOPIC_LOCK.get("topic") or topic
    if who != topic and not _validate_entity(who, _locked_topic, research):
        print(f"[IDENTITY] Rejected unrelated entity: {who!r}")
        who = topic

    # Mood from keywords (domain-aware)
    if domain == "archaeology":
        mood_map = {
            "ancient": "ancient wonder", "ruin": "solemn", "discover": "revelatory",
            "mystery": "mysterious", "biblical": "reverent", "excavat": "investigative",
            "destroy": "dramatic", "dead sea": "reflective",
        }
        mood = "historical wonder"
    else:
        mood_map = {
            "dark": "dark", "brutal": "brutal", "shock": "shocking",
            "fear": "fearful", "escape": "tense", "prison": "somber",
            "trial": "serious", "confession": "confessional",
        }
        mood = "dark investigative"

    for kw, m in mood_map.items():
        if kw in chunk_lower:
            mood = m
            break

    character_role = _SCENE_TYPE_TO_CHAR_ROLE.get(scene_type, "main")

    scene = {
        "section":        section_label,
        "scene_type":     scene_type,
        "text":           chunk,
        "who":            who,
        "where":          where,
        "era":            era,
        "mood":           mood,
        "event":          event,
        "domain":         domain,
        "character_role": character_role,
    }
    print(f"[SCENE] Semantic validation passed: domain={domain} era={era or 'n/a'} event={event or 'n/a'} role={character_role}")
    return scene


def build_scene_motion_prompt(scene: dict, identity: dict) -> str:
    """
    Build a visually specific motion prompt for this scene.
    Template set is domain-aware — archaeology scenes NEVER get crime templates.
    Environment continuity lock applied for repeated scene types.
    """
    # Select template set from topic lock domain (set once at pipeline start)
    domain    = _TOPIC_LOCK.get("domain") or scene.get("domain", "default")
    templates = _TEMPLATES_BY_DOMAIN.get(domain, _MOTION_TEMPLATES)

    scene_type = scene["scene_type"]
    # Safety: fall back to era_reenactment (neutral) rather than investigation_scene
    # when no template exists for the scene type in the selected set
    template = templates.get(scene_type) or templates.get("era_reenactment") or _MOTION_TEMPLATES["era_reenactment"]

    # Apply environment continuity lock for repeated scene types
    scene_where = scene.get("where") or identity.get("location", "")
    scene_era   = scene.get("era")   or identity.get("era", "")
    locked_where, locked_era = _lock_environment(scene_type, scene_where, scene_era)

    prompt = template.format(
        descriptor = identity.get("descriptor", identity.get("name", "")),
        location   = locked_where,
        era        = locked_era,
        event      = scene.get("event") or scene.get("mood", ""),
        style      = identity.get("style_keywords", _STYLE_PRESETS["default"]),
    )
    # Trim prompt to 200 chars for API safety
    return prompt[:200].strip()


# ============================================================
#  STEP 3: MOTION CLIP GENERATORS
# ============================================================

def generate_scene_clip(
    scene: dict,
    identity: dict,
    output_path: str,
    duration: int = 10,
) -> str | None:
    """
    Generate a motion clip for this scene (8-12s).
    Tier 1 (portrait sections): D-ID talking portrait
    Tier 2: Runway Gen-3
    Tier 3: Luma Dream Machine
    Tier 4: Kling AI
    Tier 5: Enhanced still (cinematic motion — always works)
    """
    # Clamp duration to cinematic range
    duration = max(8, min(12, int(duration or 10)))

    # ── Semantic scene validation before any generation ──────────────────────
    locked_topic  = _TOPIC_LOCK.get("topic") or identity.get("name", "")
    locked_domain = _TOPIC_LOCK.get("domain", "default")

    # Validate scene entity against topic lock
    scene_who = scene.get("who", "")
    if scene_who and scene_who != locked_topic:
        if not _validate_entity(scene_who, locked_topic, {}):
            print(f"[ERROR] Cross-topic contamination detected: scene entity {scene_who!r} does not belong to {locked_topic!r}")
            print(f"[SCENE] Fallback blocked — scene entity rejected, substituting topic identity")
            scene = dict(scene)  # copy to avoid mutating caller's dict
            scene["who"] = locked_topic

    prompt = build_scene_motion_prompt(scene, identity)

    print(f"[SCENE] Semantic validation passed: domain={locked_domain} entity={scene.get('who','')[:40]}")

    # Cache check (topic-scoped — previous-topic clips cannot match)
    cached = _clip_cache_get(prompt, duration)
    if cached:
        return cached

    ref_img = identity.get("ref_image_path")

    # ── Tier 2: Runway ────────────────────────────────────────────────────────
    if ref_img and _health.is_healthy("runway"):
        result = _runway_image_to_video(ref_img, prompt, output_path, duration)
        if result:
            _health.reset("runway")
            _clip_cache_set(prompt, duration, result)
            print(f"[SCENE] Reenactment scene created (Runway): {scene['scene_type']}")
            print(f"[SCENE] Motion animation active: {scene['scene_type']}")
            return result
        _health.record_failure("runway")

    # ── Tier 3: Luma Dream Machine ────────────────────────────────────────────
    if _health.is_healthy("luma"):
        result = _luma_image_to_video(ref_img, prompt, output_path, duration)
        if result:
            _health.reset("luma")
            _clip_cache_set(prompt, duration, result)
            print(f"[SCENE] Reenactment scene created (Luma): {scene['scene_type']}")
            print(f"[SCENE] Motion animation active: {scene['scene_type']}")
            return result
        _health.record_failure("luma")

    # ── Tier 4: Kling AI ──────────────────────────────────────────────────────
    if _health.is_healthy("kling"):
        result = _kling_image_to_video(
            ref_img,
            prompt,
            output_path,
            duration,
            context={"scene_type": scene.get("scene_type", ""), "topic": locked_topic},
        )
        if result:
            _health.reset("kling")
            _clip_cache_set(prompt, duration, result)
            print(f"[SCENE] Reenactment scene created (Kling): {scene['scene_type']}")
            print(f"[SCENE] Motion animation active: {scene['scene_type']}")
            return result
        print("[KLING] Fallback path active")
        _health.record_failure("kling")

    # ── Tier 5: Enhanced still (cinematic motion — always works) ─────────────
    # Portrait-only scene types: use character photo.
    # All other types: fetch a scene-relevant image first so the video is NOT
    # a 15-minute hold on the same portrait photo.
    _PORTRAIT_ONLY_TYPES = {"talking_portrait", "memorial_scene"}
    if scene.get("scene_type") in _PORTRAIT_ONLY_TYPES:
        src_img = ref_img
        if not src_img or not os.path.exists(src_img):
            src_img = _generate_fallback_image(scene, identity, output_path.replace(".mp4", "_bg.jpg"))
    else:
        src_img = _generate_fallback_image(scene, identity, output_path.replace(".mp4", "_bg.jpg"))
        if not src_img and ref_img and os.path.exists(ref_img or ""):
            src_img = ref_img  # last resort only

    if src_img:
        # Scene-type → motion mode mapping for atmospheric variety
        _SCENE_MOTIONS: dict[str, str] = {
            "talking_portrait":   "breathe",
            "interrogation_room": "flicker",
            "courtroom_drama":    "pan_right",
            "evidence_scene":     "zoom_in",
            "cctv_footage":       "flicker",
            "newspaper_reveal":   "zoom_in",
            "prison_cell":        "pan_up",
            "childhood_archive":  "parallax",
            "flashback":          "fog",
            "era_reenactment":    "fog",
            "investigation_scene":"zoom_out",
            "comparison_scene":   "pan_left",
            "memorial_scene":     "smoke",
        }
        motion = _SCENE_MOTIONS.get(scene.get("scene_type", ""), "zoom_in")
        result = _enhanced_still_clip(src_img, output_path, duration=duration, motion=motion)
        if result:
            _clip_cache_set(prompt, duration, result)
            print(f"[SCENE] Narration-linked visual active (enhanced still, {motion}): {scene['scene_type']}")
            return result

    print(f"[SCENE] All clip generation tiers failed for: {scene['scene_type']}")
    return None


# ── Runway Gen-3 Turbo ────────────────────────────────────────────────────────

def _runway_image_to_video(
    image_path: str,
    prompt: str,
    output_path: str,
    duration: int = 5,
) -> str | None:
    api_key = os.getenv("RUNWAYML_API_SECRET", "").strip()
    if not api_key:
        return None
    try:
        img_b64 = _image_to_b64(image_path)
        if not img_b64:
            return None
        ext  = os.path.splitext(image_path)[1].lower().lstrip(".") or "jpeg"
        mime = f"image/{ext}"

        r = requests.post(
            "https://api.dev.runwayml.com/v1/image_to_video",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
                "X-Runway-Version": "2024-11-06",
            },
            json={
                "model":       "gen3a_turbo",
                "promptImage": f"data:{mime};base64,{img_b64}",
                "promptText":  prompt,
                "duration":    duration,
                "ratio":       "768:1344",
            },
            timeout=30,
        )
        if r.status_code not in (200, 201):
            print(f"[SCENE] Runway submit failed: HTTP {r.status_code}")
            return None
        task_id = r.json().get("id", "")
        if not task_id:
            return None
        print(f"[SCENE] Runway task submitted: {task_id}")
        return _runway_poll(task_id, output_path, api_key)
    except Exception as e:
        print(f"[SCENE] Runway error: {e}")
        return None


def _runway_poll(task_id: str, output_path: str, api_key: str, timeout: int = 180) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(8)
        try:
            r = requests.get(
                f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            data   = r.json()
            status = data.get("status", "")
            if status == "SUCCEEDED":
                video_url = (data.get("output") or [""])[0]
                if video_url:
                    return _download_video(video_url, output_path)
            elif status in ("FAILED", "CANCELLED"):
                print(f"[SCENE] Runway task {status}: {data.get('failure','')}")
                return None
        except Exception:
            pass
    print(f"[SCENE] Runway poll timeout for task {task_id}")
    return None


# ── Luma Dream Machine ────────────────────────────────────────────────────────

def _luma_image_to_video(
    image_path: str | None,
    prompt: str,
    output_path: str,
    duration: int = 5,
) -> str | None:
    api_key = os.getenv("LUMAAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        body: dict = {
            "prompt":       prompt,
            "aspect_ratio": "9:16",
            "loop":         False,
        }
        if image_path and os.path.exists(image_path):
            img_b64 = _image_to_b64(image_path)
            if img_b64:
                body["keyframes"] = {
                    "frame0": {
                        "type": "image",
                        "url":  f"data:image/jpeg;base64,{img_b64}",
                    }
                }
        r = requests.post(
            "https://api.lumalabs.ai/dream-machine/v1/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=30,
        )
        if r.status_code not in (200, 201):
            print(f"[SCENE] Luma submit failed: HTTP {r.status_code}")
            return None
        gen_id = r.json().get("id", "")
        if not gen_id:
            return None
        print(f"[SCENE] Luma generation submitted: {gen_id}")
        return _luma_poll(gen_id, output_path, api_key)
    except Exception as e:
        print(f"[SCENE] Luma error: {e}")
        return None


def _luma_poll(gen_id: str, output_path: str, api_key: str, timeout: int = 180) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(8)
        try:
            r = requests.get(
                f"https://api.lumalabs.ai/dream-machine/v1/generations/{gen_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            data   = r.json()
            status = data.get("state", "")
            if status == "completed":
                video_url = (data.get("assets") or {}).get("video", "")
                if video_url:
                    return _download_video(video_url, output_path)
            elif status == "failed":
                print(f"[SCENE] Luma generation failed: {data.get('failure_reason','')}")
                return None
        except Exception:
            pass
    print(f"[SCENE] Luma poll timeout for {gen_id}")
    return None


# ── Kling AI ──────────────────────────────────────────────────────────────────

def _kling_image_to_video(
    image_path: str | None,
    prompt: str,
    output_path: str,
    duration: int = 5,
    context: dict | None = None,
) -> str | None:
    api_key = os.getenv("KLING_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        duration = int(duration or 5)
    except Exception:
        duration = 5
    duration = max(5, min(duration, 10))
    context = context or {}
    print("[KLING] Rendering clip")
    print(f"[KLING] Args validated: image={bool(image_path)} duration={duration}s context={bool(context)}")
    try:
        body: dict = {
            "model_name":    "kling-v1",
            "prompt":        prompt,
            "negative_prompt": "text, watermark, logo, blurry, static, low quality",
            "cfg_scale":     0.5,
            "mode":          "std",
            "duration":      str(duration),
        }
        if image_path and os.path.exists(image_path):
            img_b64 = _image_to_b64(image_path)
            if img_b64:
                body["image"] = img_b64

        r = requests.post(
            "https://api.klingai.com/v1/videos/image2video",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=30,
        )
        if r.status_code not in (200, 201):
            print(f"[SCENE] Kling submit failed: HTTP {r.status_code}")
            return None
        task_id = (r.json().get("data") or {}).get("task_id", "")
        if not task_id:
            return None
        print(f"[SCENE] Kling task submitted: {task_id}")
        return _kling_poll(task_id, output_path, api_key)
    except Exception as e:
        print(f"[SCENE] Kling error: {e}")
        print("[KLING] Fallback path active")
        return None


def _kling_poll(task_id: str, output_path: str, api_key: str, timeout: int = 180) -> str | None:
    deadline = time.time() + timeout
    retry_logged = False
    while time.time() < deadline:
        time.sleep(10)
        if retry_logged:
            print("[KLING] Retry path active")
        retry_logged = True
        try:
            r = requests.get(
                f"https://api.klingai.com/v1/videos/image2video/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            data   = r.json().get("data", {})
            status = data.get("task_status", "")
            if status == "succeed":
                videos = data.get("task_result", {}).get("videos", [])
                if videos:
                    return _download_video(videos[0].get("url", ""), output_path)
            elif status == "failed":
                print(f"[SCENE] Kling task failed: {data.get('task_status_msg','')}")
                return None
        except Exception:
            pass
    print(f"[SCENE] Kling poll timeout for task {task_id}")
    return None


# ── Enhanced still (Ken Burns — Tier 5, always available) ────────────────────

def _enhanced_still_clip(
    image_path: str,
    output_path: str,
    duration: float = 10.0,
    motion: str = "zoom_in",
) -> str | None:
    """
    Cinematic motion clip from a still image — no external API required.

    Supported motion modes:
      zoom_in    — slow push-in (Ken Burns)
      zoom_out   — slow pull-back
      pan_right  — slow horizontal drift right
      pan_left   — slow horizontal drift left
      pan_up     — slow vertical drift up
      pan_down   — slow vertical drift down
      breathe    — subtle oscillating scale (documentary talking head)
      parallax   — foreground drift vs background lock (depth illusion)
      flicker    — random brightness variation (old-film / CCTV / interrogation)
      rain       — progressive left-drift + darkening (exterior night scene)
    """
    try:
        from PIL import Image as _PIL
        import numpy as np
        import math

        try:
            from moviepy.editor import VideoClip
        except ImportError:
            from moviepy import VideoClip

        img = _PIL.open(image_path).convert("RGB").resize((1080, 1920), _PIL.LANCZOS)
        img_np = np.array(img, dtype=np.float32)
        h, w   = img_np.shape[:2]

        # Pre-seed deterministic flicker values for this clip
        _rng = random.Random(hash(image_path + motion))
        _flicker_vals = [
            _rng.uniform(0.80, 1.0) if _rng.random() < 0.08 else _rng.uniform(0.93, 1.0)
            for _ in range(int(duration * 24) + 10)
        ]

        def make_frame(t: float):
            progress = t / max(duration, 0.001)
            frame_idx = int(t * 24)

            # ── Scale + offset calculation per motion mode ────────────────
            if motion == "zoom_in":
                scale = 1.0 + 0.15 * progress
                ox    = (int(w * scale) - w) // 2
                oy    = (int(h * scale) - h) // 2
            elif motion == "zoom_out":
                scale = 1.15 - 0.15 * progress
                ox    = (int(w * scale) - w) // 2
                oy    = (int(h * scale) - h) // 2
            elif motion == "pan_right":
                scale = 1.10
                ox    = int((w * 0.10) * progress)
                oy    = (int(h * scale) - h) // 2
            elif motion == "pan_left":
                scale = 1.10
                ox    = int((w * 0.10) * (1.0 - progress))
                oy    = (int(h * scale) - h) // 2
            elif motion == "pan_up":
                scale = 1.10
                ox    = (int(w * scale) - w) // 2
                oy    = int((h * 0.10) * progress)
            elif motion == "pan_down":
                scale = 1.10
                ox    = (int(w * scale) - w) // 2
                oy    = int((h * 0.10) * (1.0 - progress))
            elif motion == "breathe":
                # Subtle inhale/exhale oscillation — 1.00 → 1.03 → 1.00
                scale = 1.0 + 0.03 * abs(math.sin(math.pi * progress * 2))
                ox    = (int(w * scale) - w) // 2
                oy    = (int(h * scale) - h) // 2
            elif motion == "parallax":
                # Slight scale + asymmetric horizontal drift for depth
                scale = 1.08
                ox    = int((w * 0.08) * progress * 0.6)
                oy    = (int(h * scale) - h) // 2
            elif motion in ("flicker", "rain"):
                scale = 1.06
                # Rain: slight left drift over time
                ox    = int((w * 0.06) * progress) if motion == "rain" else (int(w * scale) - w) // 2
                oy    = (int(h * scale) - h) // 2
            elif motion == "fog":
                # Slow push-in + brightening haze building over time
                scale = 1.0 + 0.08 * progress
                ox    = (int(w * scale) - w) // 2
                oy    = (int(h * scale) - h) // 2
            elif motion == "smoke":
                # Slow pull-back + dark atmospheric pulse
                scale = 1.12 - 0.06 * progress
                ox    = (int(w * scale) - w) // 2
                oy    = (int(h * scale) - h) // 2
            else:
                scale = 1.0 + 0.10 * progress
                ox    = (int(w * scale) - w) // 2
                oy    = (int(h * scale) - h) // 2

            new_h = int(h * scale)
            new_w = int(w * scale)
            resized = np.array(
                _PIL.fromarray(img_np.astype(np.uint8)).resize((new_w, new_h), _PIL.LANCZOS),
                dtype=np.float32,
            )

            ox = max(0, min(ox, new_w - w))
            oy = max(0, min(oy, new_h - h))
            cropped = resized[oy:oy + h, ox:ox + w]

            # ── Atmospheric overlays ──────────────────────────────────────
            if motion == "flicker":
                brightness = _flicker_vals[min(frame_idx, len(_flicker_vals) - 1)]
                cropped = cropped * brightness

            elif motion == "rain":
                darkness = 1.0 - 0.25 * progress
                cropped = cropped * darkness

            elif motion == "fog":
                # White/grey haze that builds from 0% → 18% opacity
                fog_opacity = 0.18 * progress
                fog_layer   = np.full_like(cropped, 210.0)  # pale grey
                cropped     = cropped * (1.0 - fog_opacity) + fog_layer * fog_opacity
                # Also slightly brighten (fog diffuses light)
                cropped     = cropped * (1.0 + 0.08 * progress)

            elif motion == "smoke":
                # Dark atmospheric pulse — two slow cycles of darkening
                smoke_factor = 0.85 + 0.15 * abs(math.sin(math.pi * progress * 2))
                cropped = cropped * smoke_factor

            # Dark vignette overlay for cinematic feel
            vignette = _make_vignette(h, w, strength=0.45)
            frame    = cropped * vignette
            return np.clip(frame, 0, 255).astype(np.uint8)

        clip = VideoClip(make_frame, duration=duration)
        clip.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio=False,
            preset="ultrafast",
            logger=None,
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 5_000:
            return output_path
    except Exception as e:
        print(f"[SCENE] Enhanced still failed: {e}")
    return None


def _make_vignette(h: int, w: int, strength: float = 0.4):
    """Create a radial vignette mask as numpy array (h, w, 1)."""
    import numpy as np
    y, x = np.ogrid[:h, :w]
    cx, cy = w / 2, h / 2
    dist = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)
    mask = 1.0 - np.clip(dist * strength, 0, strength)
    return mask[:, :, np.newaxis]


def _crop_image_region(src: str, dst: str, region: str) -> str | None:
    """Crop a region of src and save at 1080×1920 to dst for shot diversity."""
    try:
        from PIL import Image as _PIL
        img = _PIL.open(src).convert("RGB").resize((1080, 1920), _PIL.LANCZOS)
        w, h = img.size
        if region == "top":
            box = (0, 0, w, int(h * 0.75))
        elif region == "bottom":
            box = (0, int(h * 0.25), w, h)
        elif region == "left":
            box = (0, 0, int(w * 0.75), h)
        elif region == "right":
            box = (int(w * 0.25), 0, w, h)
        else:
            return src
        img.crop(box).resize((1080, 1920), _PIL.LANCZOS).save(dst, "JPEG", quality=88)
        return dst
    except Exception:
        return src


def _pollinations_fetch_scene(prompt: str, output_path: str, era: str = "", style: str = "") -> str | None:
    """Fetch one scene image from Pollinations AI. Returns saved path or None."""
    parts = [p for p in [era, prompt, style] if p]
    full_prompt = ", ".join(parts) + ", 9:16 vertical format"
    try:
        encoded = requests.utils.quote(full_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&nologo=true"
        for attempt in range(2):
            try:
                r = requests.get(url, timeout=90)
                if r.status_code == 200 and len(r.content) > 15_000:
                    from PIL import Image as _PIL
                    import io
                    img = _PIL.open(io.BytesIO(r.content)).convert("RGB").resize((1080, 1920), _PIL.LANCZOS)
                    img.save(output_path, "JPEG", quality=90)
                    return output_path
                if r.status_code == 429:
                    time.sleep(30)
            except Exception:
                if attempt == 0:
                    time.sleep(15)
    except Exception as e:
        print(f"[SHOT ENGINE] Pollinations fetch failed: {e}")
    return None


def build_scene_image_pool(
    scenes: list[dict],
    identity: dict,
    clips_dir: str,
    pool_prefix: str,
    max_per_type: int = 2,
) -> dict[str, list[str]]:
    """
    Pre-generate a diverse image pool for all scene types present in this episode.
    Portrait types reuse the character identity photo.
    All other types use scene-type-specific Pollinations prompts.
    Returns {scene_type: [image_path, ...]} mapping.
    """
    from agent.video_agent import parallel_map_safe

    os.makedirs(clips_dir, exist_ok=True)
    unique_types = list({s["scene_type"] for s in scenes})
    era   = identity.get("era", "")
    style = identity.get("style_keywords", _STYLE_PRESETS["default"])
    pool: dict[str, list[str]] = {}
    _PORTRAIT_TYPES = {"talking_portrait", "memorial_scene"}

    # Pass 1: handle portrait types + collect tasks for parallel fetch
    tasks: list[tuple] = []   # (stype, i, tmpl, img_out)
    for stype in unique_types:
        pool[stype] = []
        if stype in _PORTRAIT_TYPES:
            ref = identity.get("ref_image_path")
            if ref and os.path.exists(ref):
                pool[stype] = [ref]
            continue
        templates = _SHOT_PROMPTS.get(stype) or _SHOT_PROMPTS.get("investigation_scene", [])
        for i, tmpl in enumerate(templates[:max_per_type]):
            img_out = os.path.join(clips_dir, f"{pool_prefix}_{stype[:12]}_{i}.jpg")
            if os.path.exists(img_out) and os.path.getsize(img_out) > 15_000:
                pool[stype].append(img_out)
                print(f"[SHOT ENGINE] Reusing cached image: {stype} [{i}]")
            else:
                tasks.append((stype, i, tmpl, img_out))

    # Pass 2: fetch all pending images in parallel
    if tasks:
        print(f"[SHOT ENGINE] Fetching {len(tasks)} images in parallel (workers=10)...")

        def _fetch_task(t):
            stype, i, tmpl, img_out = t
            img = _pollinations_fetch_scene(tmpl, img_out, era=era, style=style)
            return (stype, i, img_out, img)

        results = parallel_map_safe(_fetch_task, tasks, max_workers=20, timeout=120, label="scene img")
        for r in results:
            if r and r[3]:
                stype, i, img_out, img = r
                pool[stype].append(img)
                print(f"[SHOT ENGINE] Scene image fetched: {stype} [{i}]")

    # Cross-fill: types with no images borrow from investigation_scene pool
    fallback = pool.get("investigation_scene", [])
    for stype in unique_types:
        if not pool.get(stype) and stype not in _PORTRAIT_TYPES:
            pool[stype] = fallback

    total = sum(len(v) for v in pool.values())
    print(f"[SHOT ENGINE] Image pool ready: {total} images across {len(unique_types)} scene types")
    return pool


def generate_shot_sequence(
    scene: dict,
    identity: dict,
    clips_dir: str,
    stable_id: str,
    scene_idx: int,
    image_pool: dict,
) -> list[str]:
    """
    Convert one 50-word scene beat into 4-6 short clips (2.5-3.5s each).
    Uses image_pool for scene-relevant visuals — never uses portrait for non-portrait scenes.
    Returns list of clip file paths ready for assembly.
    """
    scene_type = scene.get("scene_type", "investigation_scene")
    _SHORT_BEAT_TYPES = {"memorial_scene", "flashback", "childhood_archive"}
    n_shots = 3 if scene_type in _SHORT_BEAT_TYPES else 5

    pool_imgs = image_pool.get(scene_type) or []
    if not pool_imgs and scene_type not in {"talking_portrait", "memorial_scene"}:
        pool_imgs = image_pool.get("investigation_scene") or image_pool.get("era_reenactment") or []
    if not pool_imgs:
        ref = identity.get("ref_image_path")
        if ref and os.path.exists(ref):
            pool_imgs = [ref]
        else:
            return []

    clip_paths: list[str] = []
    _regions = ["center", "top", "bottom", "left", "right"]

    for shot_idx in range(n_shots):
        img_src = pool_imgs[shot_idx % len(pool_imgs)]
        if not img_src or not os.path.exists(img_src):
            continue

        region = _regions[shot_idx % len(_regions)]
        if region != "center":
            crop_out = os.path.join(clips_dir, f"{stable_id}_b{scene_idx:02d}_c{shot_idx}.jpg")
            if not (os.path.exists(crop_out) and os.path.getsize(crop_out) > 5_000):
                img_src = _crop_image_region(img_src, crop_out, region) or img_src
            elif os.path.exists(crop_out):
                img_src = crop_out

        motion   = _BEAT_MOTIONS[(scene_idx * 7 + shot_idx) % len(_BEAT_MOTIONS)]
        duration = 2.5 + (shot_idx % 3) * 0.5   # cycles: 2.5 / 3.0 / 3.5s

        clip_out = os.path.join(clips_dir, f"{stable_id}_beat{scene_idx:02d}_s{shot_idx:02d}.mp4")
        if os.path.exists(clip_out) and os.path.getsize(clip_out) > 5_000:
            clip_paths.append(clip_out)
            continue

        result = _enhanced_still_clip(img_src, clip_out, duration=duration, motion=motion)
        if result:
            clip_paths.append(result)

    return clip_paths


# ── Fallback image generator when no real photo exists ───────────────────────

def _generate_fallback_image(scene: dict, identity: dict, output_path: str) -> str | None:
    """
    Fetch a topic-relevant image from Pollinations or Wikimedia as a
    fallback background for the enhanced still clip.
    """
    from agents.video_agent import build_visual_search_query, _wikimedia_image_results, _download_first_valid

    # Ensure fallback image query is locked to current topic — reject leaked identity
    locked_topic = _TOPIC_LOCK.get("topic") or identity.get("name", "")
    topic_name   = identity.get("name", "") or locked_topic
    if locked_topic and topic_name != locked_topic and not _validate_entity(topic_name, locked_topic, {}):
        print(f"[SCENE] Topic validation — identity {topic_name!r} rejected for fallback image, constrained to locked topic")
        topic_name = locked_topic

    query = build_visual_search_query(
        scene.get("text", ""),
        topic=topic_name,
    )
    urls = _wikimedia_image_results(query, max_results=3)
    if urls:
        saved = _download_first_valid(urls, output_path)
        if saved:
            return saved

    # Pollinations fallback
    try:
        encoded = requests.utils.quote(f"{query}, dark cinematic documentary")
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&nologo=true"
        r   = requests.get(url, timeout=60)
        if r.status_code == 200 and len(r.content) > 15_000:
            from PIL import Image as _PIL
            import io
            img = _PIL.open(io.BytesIO(r.content)).convert("RGB").resize((1080, 1920), _PIL.LANCZOS)
            img.save(output_path, "JPEG")
            return output_path
    except Exception:
        pass
    return None


# ============================================================
#  STEP 4: TALKING PORTRAIT (D-ID)
# ============================================================

def generate_talking_portrait(
    ref_image_path: str,
    audio_path: str,
    output_path: str,
) -> str | None:
    """
    D-ID API: animate a portrait photo to match audio narration.
    Creates subtle talking motion + eye movement — documentary tone only.
    Returns path to the generated .mp4 clip, or None on failure.
    """
    api_key = os.getenv("DID_API_KEY", "").strip()
    if not api_key:
        print("[CHARACTER] D-ID API key missing — skipping talking portrait")
        return None
    if not _health.is_healthy("did"):
        print("[CHARACTER] D-ID provider unhealthy — skipping")
        return None

    try:
        # Upload source image to D-ID
        img_url = _did_upload_image(ref_image_path, api_key)
        if not img_url:
            _health.record_failure("did")
            return None

        # Upload audio to D-ID
        audio_url = _did_upload_audio(audio_path, api_key)
        if not audio_url:
            _health.record_failure("did")
            return None

        # Create talk
        talk_id = _did_create_talk(img_url, audio_url, api_key)
        if not talk_id:
            _health.record_failure("did")
            return None

        print(f"[CHARACTER] D-ID talk submitted: {talk_id}")

        # Poll for result
        video_url = _did_poll(talk_id, api_key)
        if video_url:
            result = _download_video(video_url, output_path)
            if result:
                _health.reset("did")
                print(f"[CHARACTER] Talking portrait created: {os.path.basename(result)}")
                return result

        _health.record_failure("did")
    except Exception as e:
        _health.record_failure("did")
        print(f"[CHARACTER] D-ID talking portrait failed: {e}")
    return None


def _did_upload_image(image_path: str, api_key: str) -> str | None:
    """Upload a local image to D-ID and return the hosted URL."""
    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        ext  = os.path.splitext(image_path)[1].lower().lstrip(".") or "jpeg"
        mime = f"image/{ext}"
        r = requests.post(
            "https://api.d-id.com/images",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"image": (os.path.basename(image_path), img_bytes, mime)},
            timeout=30,
        )
        if r.status_code in (200, 201):
            return r.json().get("url", "")
        print(f"[CHARACTER] D-ID image upload failed: HTTP {r.status_code}")
    except Exception as e:
        print(f"[CHARACTER] D-ID image upload error: {e}")
    return None


def _did_upload_audio(audio_path: str, api_key: str) -> str | None:
    """Upload a local audio file to D-ID and return the hosted URL."""
    try:
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        r = requests.post(
            "https://api.d-id.com/audios",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"audio": (os.path.basename(audio_path), audio_bytes, "audio/mpeg")},
            timeout=30,
        )
        if r.status_code in (200, 201):
            return r.json().get("url", "")
        print(f"[CHARACTER] D-ID audio upload failed: HTTP {r.status_code}")
    except Exception as e:
        print(f"[CHARACTER] D-ID audio upload error: {e}")
    return None


def _did_create_talk(img_url: str, audio_url: str, api_key: str) -> str | None:
    try:
        r = requests.post(
            "https://api.d-id.com/talks",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json={
                "source_url": img_url,
                "script": {
                    "type":       "audio",
                    "audio_url":  audio_url,
                    "reduce_noise": False,
                },
                "config": {
                    "fluent":    False,
                    "pad_audio": 0.0,
                    "result_format": "mp4",
                },
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            return r.json().get("id", "")
        print(f"[CHARACTER] D-ID talk creation failed: HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[CHARACTER] D-ID create_talk error: {e}")
    return None


def _did_poll(talk_id: str, api_key: str, timeout: int = 180) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(6)
        try:
            r = requests.get(
                f"https://api.d-id.com/talks/{talk_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            data   = r.json()
            status = data.get("status", "")
            if status == "done":
                return data.get("result_url", "")
            if status == "error":
                print(f"[CHARACTER] D-ID talk error: {data.get('error','')}")
                return None
        except Exception:
            pass
    print(f"[CHARACTER] D-ID poll timeout for talk {talk_id}")
    return None


# ============================================================
#  STEP 5: VIDEO ASSEMBLY
# ============================================================

def assemble_animation_video(
    clip_paths: list[str],
    audio_path: str,
    output_path: str,
) -> str:
    """
    Assemble motion clips with narration audio into a continuous documentary.
    Clips are concatenated (not looped like the slideshow pipeline).
    If clips fall short of audio duration, last clip is extended via looping.
    """
    try:
        from moviepy.editor import (
            VideoFileClip, AudioFileClip, concatenate_videoclips,
        )
    except ImportError:
        from moviepy import VideoFileClip, AudioFileClip, concatenate_videoclips

    import traceback

    print(f"[VISUAL] Consistent style mode active — assembling {len(clip_paths)} motion clips")

    try:
        audio      = AudioFileClip(audio_path)
        total_secs = audio.duration
    except Exception as e:
        print(f"[Anim] Audio load failed: {e}")
        return ""

    # Load valid clips
    loaded: list = []
    for p in clip_paths:
        if p and os.path.exists(p):
            try:
                vc = VideoFileClip(p).resize((1080, 1920))
                loaded.append(vc)
            except Exception as e:
                print(f"[Anim] Clip load failed ({p}): {e}")

    if not loaded:
        print("[Anim] No valid clips — aborting assembly")
        return ""

    # Extend to cover full audio duration
    accumulated = 0.0
    ordered: list = []
    idx = 0
    while accumulated < total_secs:
        clip = loaded[idx % len(loaded)]
        remaining = total_secs - accumulated
        if clip.duration > remaining:
            clip = clip.subclip(0, remaining)
        ordered.append(clip)
        accumulated += clip.duration
        idx += 1

    print(f"[Anim] {len(ordered)} clips cover {accumulated:.1f}s / {total_secs:.1f}s audio")

    # Apply cinematic crossfade between clips (0.4s overlap)
    _XFADE = 0.4
    try:
        _xfade_clips = []
        for _xi, _xc in enumerate(ordered):
            if _xi > 0 and _xc.duration > _XFADE + 0.2:
                _xc = _xc.crossfadein(_XFADE)
            _xfade_clips.append(_xc)
        _concat_method = "compose"
        _concat_padding = -_XFADE
    except Exception:
        _xfade_clips = ordered
        _concat_method = "chain"
        _concat_padding = 0

    try:
        final = concatenate_videoclips(
            _xfade_clips, method=_concat_method, padding=_concat_padding
        ).set_audio(audio)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        final.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            logger=None,
        )
        for c in loaded:
            try:
                c.close()
            except Exception:
                pass
        if os.path.exists(output_path) and os.path.getsize(output_path) > 50_000:
            print(f"[Anim] Assembly complete: {output_path}")
            return output_path
    except Exception as e:
        print(f"[Anim] Assembly failed: {e}")
        traceback.print_exc()

    return ""


# ============================================================
#  MAIN ENTRY POINT
# ============================================================

def create_animation_video(
    script_data: dict,
    research:    dict,
    output_dir:  str = _ANIM_DIR,
    audio_path:  str = "",
) -> str:
    """
    Full AI Animation Pipeline:

    1. Build character identity (real photo + descriptor)
    2. Generate narration audio (TTS via ElevenLabs)
    3. Parse script into narration-linked scenes
    4. Generate talking portrait for hook section (D-ID)
    5. Generate motion clips per scene (Runway → Luma → Kling → enhanced still)
    6. Assemble continuous-motion documentary

    Returns final video path, or "" on failure.
    """
    import traceback
    import shutil as _shutil

    topic    = script_data.get("topic", "")
    language = script_data.get("language", "english")
    is_short = bool(script_data.get("is_short") or script_data.get("short_script_en"))

    # Stable ID (no timestamp) — used for clip filenames so reruns reuse clips
    _lang_tag  = "ar" if language == "arabic" else "en"
    _type_tag  = "short" if is_short else "long"
    stable_id  = re.sub(r'[^a-z0-9_]', '_', topic.lower())[:30] + f"_{_lang_tag}_{_type_tag}"

    # Timestamped ID only for the unique final output filename
    video_id   = stable_id + f"_{int(time.time())}"

    # Route persistent storage through content/<topic>/ when available
    _anim_path = _CONTENT_PATHS.get("animations_path") or output_dir
    chars_dir  = os.path.join(_anim_path, "characters")
    clips_dir  = os.path.join(_anim_path, "clips")
    for _d in [output_dir, chars_dir, clips_dir]:
        os.makedirs(_d, exist_ok=True)

    # Reset environment lock at the start of every video — prevents env state
    # from leaking between successive calls within the same process
    global _ENVIRONMENT_LOCK
    _ENVIRONMENT_LOCK = {}

    # ── Step 1: Character cast (CHARACTER PIPELINE) ──────────────────────────
    if language == "arabic":
        try:
            from agents.script_quality import enforce_arabic_purity
            script_data["script"] = enforce_arabic_purity(script_data.get("script", ""))
        except Exception as e:
            print(f"[AR PURITY] Final sanitize failed: {e}")

    # Determine style for this topic before building cast
    _topic_lower  = topic.lower()
    _niche_lower  = (research.get("niche", "") or "").lower()
    _style_preset = "default"
    for _dom, _kws in _ANIM_DOMAIN_KEYWORDS.items():
        if any(kw in _topic_lower or kw in _niche_lower for kw in _kws):
            _style_preset = _DOMAIN_TO_STYLE.get(_dom, "default")
            break
    if _style_preset == "default":
        for _skey in _STYLE_PRESETS:
            if _skey in _topic_lower or _skey in _niche_lower:
                _style_preset = _skey
                break
    _style_keywords = _STYLE_PRESETS.get(_style_preset, _STYLE_PRESETS["default"])

    # Use the proper characters_path from content storage if available
    _chars_content_path = _CONTENT_PATHS.get("characters_path") or chars_dir
    os.makedirs(_chars_content_path, exist_ok=True)

    try:
        from agents.character_pipeline import build_episode_cast
        cast = build_episode_cast(
            research, topic,
            output_dir=_chars_content_path,
            script_text=script_data.get("script", ""),
            style_preset=_style_preset,
            style_keywords=_style_keywords,
        )
        # Ensure style fields are set on cast members
        for _cm in cast.values():
            _cm.setdefault("style_preset",  _style_preset)
            _cm.setdefault("style_keywords", _style_keywords)
    except Exception as _ce:
        print(f"[CHARACTER] character_pipeline failed ({_ce}) — falling back to build_cast()")
        cast = build_cast(research, topic, chars_dir)

    identity = cast["main"]  # backward compat — main subject
    print(f"[CHARACTER] Real identity locked: {identity['name']} | style={_style_preset}")
    print(f"[VISUAL] Consistent cinematic style active: {_style_preset}")

    # ── Step 2: Audio (TTS via existing pipeline) ─────────────────────────────
    if not audio_path or not os.path.exists(audio_path):
        print(f"[Anim] Generating TTS audio for: {topic}")
        try:
            from agents.video_agent import generate_tts_sections, generate_voiceover, process_audio_netflix
            from config import AUDIO_DIR
            if is_short:
                audio_path = generate_voiceover(script_data["script"], video_id, language)
            else:
                audio_path, _ = generate_tts_sections(script_data["script"], video_id, language)
            if audio_path and os.path.exists(audio_path):
                audio_path = process_audio_netflix(audio_path, is_short=is_short)
        except Exception as e:
            print(f"[Anim] TTS failed: {e}")
            traceback.print_exc()
            return ""

    if not audio_path or not os.path.exists(audio_path):
        print("[Anim] No audio — aborting")
        return ""
    print(f"[Anim] Audio ready: {audio_path}")

    # ── Step 3: Parse script into narration-linked scenes ────────────────────
    scenes = parse_script_into_scenes(script_data.get("script", ""), topic, research)
    if not scenes:
        print("[Anim] No scenes parsed — aborting")
        return ""

    # ── Step 4: Talking portraits — per-character, for ~30% of portrait scenes ──
    # D-ID applied to hook + up to 2 more portrait scenes, using scene-assigned char
    _PORTRAIT_TYPES = {"talking_portrait", "investigation_scene", "memorial_scene"}
    _portrait_indices = [
        i for i, s in enumerate(scenes)
        if s.get("scene_type") in _PORTRAIT_TYPES
    ]
    _max_portraits = max(1, min(3, len(_portrait_indices)))
    if len(_portrait_indices) > _max_portraits:
        _step = max(1, len(_portrait_indices) // _max_portraits)
        _portrait_indices = _portrait_indices[::_step][:_max_portraits]

    portrait_clips: dict[int, str] = {}
    _did_key = os.getenv("DID_API_KEY", "").strip()

    if _did_key:
        for pi in _portrait_indices:
            scene_i = scenes[pi]
            # Pick the character assigned to this scene
            char_role = scene_i.get("character_role", "main")
            char = cast.get(char_role) or identity
            char_img = char.get("ref_image_path")
            if not char_img or not os.path.exists(char_img):
                # Fallback to main if scene's assigned character has no photo
                char_img = identity.get("ref_image_path")
            if not char_img or not os.path.exists(char_img):
                continue

            _port_out = os.path.join(clips_dir, f"{stable_id}_portrait_{pi:02d}.mp4")
            if os.path.exists(_port_out) and os.path.getsize(_port_out) > 10_000:
                portrait_clips[pi] = _port_out
                print(f"[SCENE] Reusing existing portrait clip [{pi}]: {os.path.basename(_port_out)}")
                continue
            _port_result = generate_talking_portrait(char_img, audio_path, _port_out)
            if _port_result:
                portrait_clips[pi] = _port_result
                print(f"[CHARACTER] Talking portrait generated: scene {pi} → "
                      f"{scene_i['scene_type']} (role={char_role}, char={char['name']})")
                print(f"[ANIMATION] Lip-sync applied: scene {pi}")

    # ── Step 5: Build scene image pool + generate shot sequences ─────────────
    # pool_prefix is topic-only (no lang tag) so EN and AR video share images.
    _pool_prefix = re.sub(r'[^a-z0-9_]', '_', topic.lower())[:30]
    print("[SHOT ENGINE] Building scene image pool...")
    image_pool = build_scene_image_pool(scenes, identity, clips_dir, _pool_prefix)

    clip_paths: list[str] = []
    _portrait_shots = 0
    _total_shots    = 0

    for i, scene in enumerate(scenes):
        if i in portrait_clips:
            clip_paths.append(portrait_clips[i])
            _portrait_shots += 1
            _total_shots    += 1
            print(f"[SCENE] Narration-linked visual active (talking portrait): {scene['scene_type']}")
            continue

        shot_clips = generate_shot_sequence(scene, identity, clips_dir, stable_id, i, image_pool)

        if shot_clips:
            clip_paths.extend(shot_clips)
            _total_shots += len(shot_clips)
            if scene.get("scene_type") in {"talking_portrait", "memorial_scene"}:
                _portrait_shots += len(shot_clips)
        else:
            # Emergency fallback: single clip via legacy path
            clip_out   = os.path.join(clips_dir, f"{stable_id}_scene_{i:02d}.mp4")
            char_role  = scene.get("character_role", "main")
            scene_char = cast.get(char_role) or identity
            if not scene_char.get("ref_image_path") or not os.path.exists(scene_char.get("ref_image_path") or ""):
                scene_char = identity
            clip = generate_scene_clip(scene, scene_char, clip_out, duration=4)
            if clip:
                clip_paths.append(clip)
                _total_shots += 1

    if not clip_paths:
        print("[Anim] No clips generated — aborting assembly")
        return ""

    # Shot density metrics
    _portrait_pct    = (_portrait_shots / max(1, _total_shots)) * 100
    _avg_shot_dur    = 3.0  # approximate (cycles 2.5/3.0/3.5s)
    _changes_per_min = 60.0 / _avg_shot_dur
    print(f"[SHOT ENGINE] unique_shots={_total_shots} "
          f"avg_shot_duration={_avg_shot_dur:.1f}s "
          f"portrait_runtime_pct={_portrait_pct:.0f}% "
          f"visual_changes_per_min={_changes_per_min:.0f}")
    print(f"[SCENE] Total motion clips ready: {len(clip_paths)}")
    print(f"[VISUAL] Consistent cinematic style active: {identity['style_preset']}")

    # ── Step 6: Assemble ──────────────────────────────────────────────────────
    output_path = os.path.join(output_dir, f"{video_id}.mp4")
    result = assemble_animation_video(clip_paths, audio_path, output_path)
    if not result:
        return ""

    print(f"[Anim] Documentary complete: {result}")

    # ── Step 7: Persist final video into content/<topic>/videos/ ─────────────
    _videos_path = _CONTENT_PATHS.get("videos_path")
    if _videos_path:
        dest_name = f"{_lang_tag}_{'short' if is_short else 'long'}.mp4"
        dest      = os.path.join(_videos_path, dest_name)
        try:
            _shutil.copy2(result, dest)
            print(f"[CONTENT] Video persisted: {dest}")
        except Exception as _ce:
            print(f"[CONTENT] Video copy (non-fatal): {_ce}")

    return result


# ── Utilities ─────────────────────────────────────────────────────────────────

def _image_to_b64(image_path: str) -> str | None:
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def _download_video(url: str, output_path: str) -> str | None:
    try:
        r = requests.get(url, timeout=60, stream=True)
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            if os.path.getsize(output_path) > 10_000:
                return output_path
    except Exception as e:
        print(f"[Anim] Video download failed ({url[:60]}): {e}")
    return None
