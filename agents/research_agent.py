# ============================================================
#  agents/research_agent.py
#  Wikipedia (primary) + DuckDuckGo (fallback) + Groq
# ============================================================
import random
import json
import time
import datetime
import hashlib
import re
import requests
from pathlib import Path

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS


def _ddgs_proxy() -> str | None:
    """Return proxy URL for DDGS if configured, else None.
    Set DDG_PROXY in GitHub Secrets to bypass GitHub Actions IP blocks.
    Supports http://user:pass@host:port and socks5://host:port formats.
    Also respects standard HTTPS_PROXY / https_proxy env vars.
    """
    import os as _os
    return (
        _os.getenv("DDG_PROXY")
        or _os.getenv("HTTPS_PROXY")
        or _os.getenv("https_proxy")
        or None
    )

import groq as groq_lib
from groq import Groq
import os
from config import GROQ_API_KEY, NICHES, NICHE_WEIGHTS
from agents.json_utils import safe_json_parse, is_valid_json_response, strip_markdown_fences, normalize_ai_json_response

_groq = Groq(api_key=GROQ_API_KEY)

_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",   # primary
    "llama-3.1-8b-instant",      # fallback
]

# Session-level Groq disable flag — set when rate-limited to skip all Groq calls this run
_GROQ_DISABLED       = False
_GROQ_DISABLED_UNTIL = 0.0   # epoch seconds

# Session-level OpenAI disable flag
_OPENAI_RESEARCH_FAILED = False


def _groq_call(**kwargs):
    """Try each model with one retry on rate limit. Sets session disable flag on persistent 429."""
    global _GROQ_DISABLED, _GROQ_DISABLED_UNTIL

    if _GROQ_DISABLED and time.time() < _GROQ_DISABLED_UNTIL:
        raise groq_lib.RateLimitError(
            "Groq disabled for this session (rate-limited earlier)",
            response=None, body=None,
        )

    last_err = None
    for model in _FALLBACK_MODELS:
        for attempt in range(2):
            try:
                time.sleep(3)
                return _groq.chat.completions.create(model=model, **kwargs)
            except groq_lib.RateLimitError as e:
                last_err = e
                if attempt == 0:
                    print(f"[Groq] Rate limit hit — waiting 40 seconds...")
                    time.sleep(40)
                else:
                    print(f"[Groq] Rate limit again on {model}, trying next model...")
                    break
            except groq_lib.BadRequestError as e:
                print(f"[Groq] BadRequestError on {model}, trying next model...")
                last_err = e
                break
            except Exception as e:
                print(f"[Groq] Unexpected error on {model}: {e}")
                last_err = e
                break

    # Disable Groq for 30 minutes if we exhausted all retries on rate limit
    if last_err and "rate" in str(last_err).lower():
        _GROQ_DISABLED       = True
        _GROQ_DISABLED_UNTIL = time.time() + 1800
        print(f"[Groq] Session disabled for 30 min due to persistent rate limit")

    if last_err:
        raise last_err
    raise RuntimeError("[Groq] All models exhausted with no error recorded")


def _ai_call_openai(prompt: str, temperature: float, max_tokens: int,
                    json_mode: bool) -> str:
    """OpenAI fallback for research calls (gpt-4o-mini)."""
    global _OPENAI_RESEARCH_FAILED
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or _OPENAI_RESEARCH_FAILED:
        return ""
    try:
        body: dict = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
        if r.status_code == 200:
            print("[Research] OpenAI fallback used for research call")
            return r.json()["choices"][0]["message"]["content"]
        if r.status_code == 429:
            _OPENAI_RESEARCH_FAILED = True
            print("[Research] OpenAI quota exceeded — disabling for this session")
        else:
            print(f"[Research] OpenAI returned HTTP {r.status_code}")
    except Exception as e:
        print(f"[Research] OpenAI fallback failed: {e}")
    return ""


def _ai_call(prompt: str, temperature: float = 0.3,
             max_tokens: int = 1000, json_mode: bool = True) -> str:
    """Research AI call: Groq primary -> OpenAI fallback -> empty string on failure."""
    max_chars = 3000
    if len(prompt) > max_chars:
        half    = max_chars // 2
        _prompt = prompt[:half] + "\n...\n" + prompt[-half:]
        print(f"[Research] Prompt truncated to {max_chars} chars")
    else:
        _prompt = prompt

    # ── Groq primary ─────────────────────────────────────────────────────────
    if not (_GROQ_DISABLED and time.time() < _GROQ_DISABLED_UNTIL):
        try:
            kwargs: dict = dict(
                messages=[{"role": "user", "content": _prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            result = _groq_call(**kwargs).choices[0].message.content
            if result:
                return result
        except Exception as e:
            print(f"[Research] Groq call failed: {e} — trying OpenAI fallback")

    # ── OpenAI fallback ───────────────────────────────────────────────────────
    result = _ai_call_openai(_prompt, temperature, max_tokens, json_mode)
    if result:
        return result

    print("[Fallback] All AI providers failed for research call — returning empty")
    return ""


COVERED_TOPICS_PATH = Path("output/covered_topics.json")


# ── Fictional vs real-story show detection ──────────────────

FICTIONAL_SHOWS = [
    "pieces of her",
    "jane queller",
    "john wick",
    "jack reacher",
    "yellowstone",
    "suits",
    "house of cards",
    "game of thrones",
    "stranger things",
    "the crown",        # dramatized, not documentary
    "breaking bad",     # walter white is fictional
    "dexter",           # dexter morgan is fictional
    "money heist",      # fictional heist
    "ozark",            # fictional family
    "squid game",
]

REAL_STORY_SHOWS = [
    "narcos",
    "boardwalk empire",
    "american gangster",
    "goodfellas",
    "casino",
    "the godfather",
    "scarface",
    "griselda",
    "el chapo",
    "dahmer",
    "monster",
    "mindhunter",
    "night stalker",
    "black mass",
    "extremely wicked",
    "wolf of wall street",
    "city of god",
    "blow",
    "donnie brasco",
    # Global additions
    "house of saddam",
    "juhayman",
    "agent ramzy",
    "rafat el hagan",
    "al hayba",
    "legend",
    "mcmafia",
    "tokyo vice",
    "baghdad central",
    "fauda",
    "gomorrah",
    "zeroerozero",
    "suburra",
    "il traditore",
    "the traitor",
    "king farouk",
    "sadat",
    "great train robbery",
]

GLOBAL_NICHES = [
    # Arabic content — high demand
    "رأفت الهجان القصة الحقيقية",
    "جهيمان العتيبي الحادثة الحقيقية",
    "بيت صدام حسين المسلسل",
    "الملك فاروق القصة الحقيقية",
    "السادات الفيلم الحقيقي",
    # Gulf specific
    "true crime Saudi Arabia documentary",
    "UAE crime documentary series",
    "Iraq war crime story film",
    "Egypt crime documentary",
    # International with Arabic connection
    "Fauda Israeli series real story",
    "Baghdad Central series true story",
    "Paranormal Egypt series real events",
    # Classic international
    "Kray twins Legend movie real story",
    "McMafia real Russian mafia story",
    "Tokyo Vice real yakuza story",
    "Gomorrah Italian mafia true story",
    "Suburra Netflix Italy real story",
    "ZeroZeroZero real cartel story",
]


# ── Known show → character/real-person map ─────────────────
# Used when Wikipedia extraction fails or as a seed for the prompt.
_KNOWN_SHOW_CHARACTERS: dict[str, list[dict]] = {
    "mindhunter": [
        {"character": "Holden Ford",  "actor": "Jonathan Groff",  "based_on": "John Douglas",   "real_role": "FBI agent who pioneered criminal profiling"},
        {"character": "Bill Tench",   "actor": "Holt McCallany",  "based_on": "Robert Ressler",  "real_role": "FBI agent and co-creator of criminal profiling"},
        {"character": "Wendy Carr",   "actor": "Anna Torv",       "based_on": "Ann Burgess",     "real_role": "Criminologist and academic partner to the BSU"},
    ],
    "narcos": [
        {"character": "Pablo Escobar", "actor": "Wagner Moura",   "based_on": "Pablo Escobar",   "real_role": "Medellín Cartel founder"},
        {"character": "Steve Murphy",  "actor": "Boyd Holbrook",  "based_on": "Steve Murphy",    "real_role": "DEA agent who hunted Escobar"},
        {"character": "Javier Peña",   "actor": "Pedro Pascal",   "based_on": "Javier Peña",     "real_role": "DEA agent, partner of Murphy"},
    ],
    "boardwalk empire": [
        {"character": "Nucky Thompson", "actor": "Steve Buscemi",  "based_on": "Enoch 'Nucky' Johnson", "real_role": "Atlantic City political boss and bootlegger"},
        {"character": "Jimmy Darmody",  "actor": "Michael Pitt",   "based_on": "Various real figures",  "real_role": "Composite character"},
    ],
    "griselda": [
        {"character": "Griselda Blanco", "actor": "Sofía Vergara", "based_on": "Griselda Blanco", "real_role": "Medellín Cartel cocaine trafficker, 'Godmother of Cocaine'"},
    ],
    "dahmer": [
        {"character": "Jeffrey Dahmer", "actor": "Evan Peters",    "based_on": "Jeffrey Dahmer",  "real_role": "Serial killer who murdered 17 men 1978–1991"},
    ],
    "wolf of wall street": [
        {"character": "Jordan Belfort",  "actor": "Leonardo DiCaprio", "based_on": "Jordan Belfort",  "real_role": "Stockbroker convicted of securities fraud"},
        {"character": "Donnie Azoff",    "actor": "Jonah Hill",         "based_on": "Danny Porush",    "real_role": "Belfort's business partner at Stratton Oakmont"},
    ],
    "american gangster": [
        {"character": "Frank Lucas",     "actor": "Denzel Washington", "based_on": "Frank Lucas",     "real_role": "Harlem drug trafficker who imported heroin from Southeast Asia"},
        {"character": "Richie Roberts",  "actor": "Russell Crowe",     "based_on": "Richie Roberts",  "real_role": "NBNDD detective who built the case against Lucas"},
    ],
    "black mass": [
        {"character": "Whitey Bulger",  "actor": "Johnny Depp",      "based_on": "James 'Whitey' Bulger", "real_role": "Winter Hill Gang boss and FBI informant"},
        {"character": "John Connolly",  "actor": "Joel Edgerton",    "based_on": "John Connolly",          "real_role": "FBI agent who protected Bulger"},
    ],
    "donnie brasco": [
        {"character": "Donnie Brasco",  "actor": "Johnny Depp",       "based_on": "Joseph D. Pistone", "real_role": "FBI undercover agent who infiltrated the Bonanno crime family"},
        {"character": "Lefty Ruggiero", "actor": "Al Pacino",         "based_on": "Benjamin Ruggiero", "real_role": "Bonanno crime family member who sponsored Pistone"},
    ],
    "tokyo vice": [
        {"character": "Jake Adelstein", "actor": "Ansel Elgort",     "based_on": "Jake Adelstein",    "real_role": "American journalist at Yomiuri Shimbun who covered yakuza"},
    ],
}

_SHOW_TRIGGER_KEYWORDS = {"netflix", "hbo", "amazon", "show", "series", "season", "episode", "tv show", "streaming"}

# ── Explicit adaptation markers — REQUIRED to activate show-cast logic ────────
# A topic must contain one of these to be treated as a TV/film adaptation.
# Keyword-only overlap (e.g. a show title appearing inside a different phrase)
# is not sufficient.  This prevents "Ancient City of Gomorrah" from triggering
# Gomorrah-series cast extraction.
_ADAPTATION_EXPLICIT_MARKERS: frozenset = frozenset({
    "series", "movie", "film", "netflix", "hbo", "amazon", "showtime",
    "real story behind", "true story behind", "inspired by", "based on",
    "tv show", "season ", "episode", "streaming", "adaptation",
    "القصة الحقيقية وراء", "الفيلم الحقيقي", "المسلسل الحقيقي",
})

# ── Semantic domain keyword sets ──────────────────────────────────────────────
_DOMAIN_KEYWORDS: dict[str, frozenset] = {
    "archaeology": frozenset({
        "archaeolog", "excavat", "ancient city", "ancient civili", "biblical",
        "bronze age", "iron age", "prehistoric", "dig site", "dead sea",
        "jordan valley", "holy land", "mesopotamia", "sodom", "gomorrah",
        "jericho", "pompeii", "tomb", "unearthed", "radiocarbon", "ruins",
        "ancient discovery", "archaeological discovery",
    }),
    "serial_killer": frozenset({
        "serial killer", "serial murder", "dahmer", "bundy", "gacy", "btk",
        "zodiac", "ripper", "strangler", "cannibal",
    }),
    "organized_crime": frozenset({
        "mafia", "cartel", "mob", "camorra", "yakuza", "triads", "triad",
        "drug lord", "crime boss", "gang lord", "narco",
    }),
    "fraud": frozenset({
        "fraud", "ponzi", "embezzl", "insider trading", "wall street broker",
        "securities fraud", "stockbroker",
    }),
    "war_historical": frozenset({
        "world war", "civil war", "genocide", "holocaust", "revolution",
        "assassination", "empire", "dynasty",
    }),
}


def classify_topic_domain(topic: str) -> str:
    """
    Classify a topic into its primary semantic domain BEFORE any research.

    Returns one of:
      tv_adaptation   — explicit show/movie/series reference present
      archaeology     — ancient sites, biblical, excavation
      serial_killer   — serial murder, specific killers
      organized_crime — mafia, cartel, gang
      fraud           — financial crime, scam
      war_historical  — war, genocide, historical event
      default         — general true crime / biography

    ONLY tv_adaptation activates show-cast extraction logic.
    """
    t = topic.lower()

    # TV adaptation: ONLY if topic explicitly contains adaptation markers
    if any(marker in t for marker in _ADAPTATION_EXPLICIT_MARKERS):
        print(f"[DOMAIN] tv_adaptation (explicit adaptation marker in topic)")
        return "tv_adaptation"

    # Domain-specific classification (priority order)
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            print(f"[DOMAIN] {domain}")
            return domain

    # Biography: pure named entity (2–4 words, first AND last start uppercase,
    # not led by an article) — no domain keyword matched, so this is almost
    # certainly a real person's name or a named historical entity.
    # Connectors ≤3 chars (bin, de, la, van, al…) are allowed in the middle.
    _w = topic.strip().split()
    _NON_ARTICLE = frozenset({"the", "a", "an"})
    if (2 <= len(_w) <= 4 and
            _w[0][0].isupper() and
            _w[-1][0].isupper() and
            _w[0].lower() not in _NON_ARTICLE and
            all(w[0].isupper() or len(w) <= 3 for w in _w)):
        print("[DOMAIN] biography (named entity topic)")
        return "biography"

    print("[DOMAIN] default")
    return "default"


# ── Topic integrity: normalization, entity extraction, confidence ──────────────

_VALID_SHORT_TERMINALS: frozenset = frozenset({
    "a", "an", "the", "of", "in", "at", "by", "on", "to", "up",
    "bc", "ad", "ce", "i", "ii", "iii", "iv", "vi", "jr", "sr", "dr",
    # Common valid name/phrase endings that are short
    "me", "us", "he", "she", "we", "it", "as", "is", "was",
})

_ERA_PATTERNS_RE: list = [
    re.compile(r"\b\d{1,4}\s*bce?\b", re.I),
    re.compile(r"\b\d{1,4}\s*ad\b", re.I),
    re.compile(r"\b\d{2,4}s\b"),
    re.compile(r"\b\d{1,2}th\s+century\b", re.I),
    re.compile(r"\b(19|20)\d{2}\b"),
]

_EVENT_TYPE_KEYWORDS: dict[str, list] = {
    "murder":         ["murder", "killing", "homicide", "slaying"],
    "fraud":          ["fraud", "scam", "ponzi", "embezzl", "swindle"],
    "archaeological": ["archaeolog", "excavat", "unearthed", "ancient city"],
    "assassination":  ["assassination", "assassin"],
    "war":            ["world war", "civil war", "battle of"],
    "heist":          ["heist", "robbery", "bank rob"],
    "cartel":         ["cartel", "drug lord", "narco"],
    "mafia":          ["mafia", "mob ", "yakuza", "camorra"],
    "serial_killing": ["serial killer", "serial murder"],
}


_SLUG_PATTERN = re.compile(r'^[a-z][a-z0-9]*(?:[_\-][a-z0-9]+)+$')


def _slug_to_title(slug: str) -> str:
    """'jeffrey_epstein' or 'jeffrey-epstein' → 'Jeffrey Epstein'"""
    return " ".join(w.capitalize() for w in re.split(r"[_\-]+", slug))


def normalize_topic_title(topic: str) -> str:
    """
    Clean and repair a topic title.

    Handles canonical slug inputs (jeffrey_epstein, ted-bundy) by converting
    to Title Case before any validation — slugs are first-class topic identifiers.

    For natural-language titles: strips trailing truncation fragments.

    Returns "" if the title is unrecoverable (too short or fully malformed).
    """
    if not topic:
        return ""
    raw = topic.strip()

    # ── Canonical slug detection (BEFORE length check) ───────────────────────
    # Slugs like jeffrey_epstein, ted-bundy, night_stalker are valid topic IDs.
    if _SLUG_PATTERN.match(raw):
        converted = _slug_to_title(raw)
        print(f"[TOPIC] Canonical slug detected: '{raw}' -> '{converted}'")
        return converted  # always accept — slugs are deterministic identifiers

    # ── Natural-language title handling ───────────────────────────────────────
    title = re.sub(r"[\s:—\-–,]+$", "", raw).strip()
    if len(title) < 15:
        return ""

    words = title.split()
    last_word = words[-1]

    if len(last_word) <= 4 and last_word.lower() not in _VALID_SHORT_TERMINALS:
        # Split on subtitle separators (: — –) and check whether the final
        # subtitle part is entirely short words (likely a truncated subtitle)
        parts = re.split(r"\s*[:\—–\-]\s*", title)
        if len(parts) >= 2:
            last_part_words = parts[-1].split()
            if all(len(w) <= 4 for w in last_part_words):
                repaired = re.sub(r"[\s:—–\-,]+$",
                                  "", " — ".join(parts[:-1])).strip()
                if len(repaired) >= 15:
                    print(f"[TOPIC NORM] Repaired truncated subtitle: "
                          f"'{topic[:70]}' → '{repaired}'")
                    return repaired
        # Fallback: strip the last word only
        repaired = re.sub(r"[\s:—–\-,]+$", "",
                          " ".join(words[:-1])).strip()
        if len(repaired) >= 15:
            print(f"[TOPIC NORM] Stripped truncated last word: "
                  f"'{topic[:70]}' → '{repaired}'")
            return repaired
        return ""

    return title


def extract_canonical_entities(topic: str) -> dict:
    """
    Extract structured entities from a topic title (no external calls).

    Returns:
        adaptation_title — show/movie title referenced in the topic, or None
        era              — year / period string found, or None
        named_persons    — Title Case proper-noun sequences (≥2 words)
        event_type       — "murder", "fraud", "archaeological", "general", etc.
        domain           — from classify_topic_domain()
    """
    t = topic.strip()
    t_lower = t.lower()

    # Adaptation title
    adaptation_title: str | None = None
    _adapt_markers = (
        "inspired by", "real story behind", "based on",
        "inspired the creation of", "the real story of",
        "true story behind", "real history behind", "creation of",
    )
    for marker in _adapt_markers:
        if marker in t_lower:
            idx = t_lower.find(marker) + len(marker)
            tail = t[idx:].strip().lstrip(": ")
            words_after = tail.split()[:6]
            if words_after:
                adaptation_title = " ".join(words_after).strip(".,: ")
            break
    if adaptation_title is None:
        for show_key in _KNOWN_SHOW_CHARACTERS:
            if show_key in t_lower:
                adaptation_title = show_key.title()
                break

    # Era
    era: str | None = None
    for pat in _ERA_PATTERNS_RE:
        m = pat.search(t)
        if m:
            era = m.group(0)
            break

    # Named persons: Title Case sequences of ≥2 words
    _SKIP_TITLE_CASE: frozenset = frozenset({
        "Real Story", "True Story", "Dark Crime", "Crime Decoded",
    })
    named_persons = [
        nm for nm in re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", t)
        if nm not in _SKIP_TITLE_CASE and len(nm.split()) <= 4
    ]

    # Event type
    event_type = "general"
    for etype, keywords in _EVENT_TYPE_KEYWORDS.items():
        if any(kw in t_lower for kw in keywords):
            event_type = etype
            break

    return {
        "adaptation_title": adaptation_title,
        "era":              era,
        "named_persons":    named_persons,
        "event_type":       event_type,
        "domain":           classify_topic_domain(t),
    }


def classify_topic_domain_with_context(topic: str, entities: dict) -> str:
    """
    Refine domain classification using extracted entity context.

    Upgrades the base domain when entity signals provide extra certainty:
    - BCE era in era field → archaeology
    - "inspired the creation of" phrase → tv_adaptation
    - Specific event types in default domain → more specific domain
    """
    base = entities.get("domain") or classify_topic_domain(topic)
    t_lower = topic.lower()
    era = (entities.get("era") or "").lower()
    event_type = entities.get("event_type") or ""

    if re.search(r"\d+\s*bce?\b", era):
        print(f"[DOMAIN CTX] BCE era '{era}' → archaeology")
        return "archaeology"

    if "inspired the creation" in t_lower or "inspired the making" in t_lower:
        print("[DOMAIN CTX] adaptation-creation pattern → tv_adaptation")
        return "tv_adaptation"

    if event_type == "archaeological":
        return "archaeology"
    if event_type in ("cartel", "mafia") and base == "default":
        return "organized_crime"
    if event_type == "serial_killing" and base == "default":
        return "serial_killer"

    return base


_CANONICAL_RESEARCH_KEYS: tuple[str, ...] = (
    "topic",
    "search_query",
    "domain",
    "topic_hash",
    "entities",
    "summary",
    "keywords",
)


def _clean_search_terms(text: str) -> list[str]:
    """Return de-duplicated, search-worthy terms in original-ish order."""
    stop = {
        "the", "a", "an", "of", "in", "on", "at", "by", "to", "for", "and",
        "or", "behind", "true", "story", "real", "what", "really", "happened",
        "movie", "film", "series", "show", "tv",
    }
    seen: set[str] = set()
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9'&.-]*", text):
        key = term.lower().strip(".,:;!?")
        if len(key) < 3 or key in stop or key in seen:
            continue
        seen.add(key)
        terms.append(term.strip(".,:;!?"))
    return terms


def generate_search_query(topic: str, entities: dict | None = None) -> str:
    """
    Build a stable web-search query from a manual or auto topic.

    Manual topics often arrive as editorial titles ("The Real Story Behind...")
    while downstream image/research code needs concise searchable entities.
    """
    topic = (topic or "").strip()
    entities = entities or extract_canonical_entities(topic)
    t_lower = topic.lower()

    people: list[str] = []
    adaptation = entities.get("adaptation_title") or ""

    for show_key, chars in _KNOWN_SHOW_CHARACTERS.items():
        if show_key in t_lower:
            adaptation = " ".join(
                word if word in {"of", "the", "and"} else word.capitalize()
                for word in show_key.split()
            )
            for char in chars:
                based_on = (char.get("based_on") or "").strip()
                if based_on and based_on.lower() in t_lower:
                    people.append(based_on)

    if not people:
        for person in entities.get("named_persons") or []:
            if person not in people and not person.lower().startswith(("the real", "real story", "true story")):
                people.append(person)

    parts: list[str] = []
    for person in people[:2]:
        parts.extend(person.split())
    if adaptation:
        parts.extend(adaptation.split())

    if not parts:
        stripped = re.sub(
            r"\b(the\s+)?(real|true)\s+story\s+(behind|of)\b",
            " ",
            topic,
            flags=re.I,
        )
        parts.extend(_clean_search_terms(stripped)[:8])

    if "real story" in t_lower or "true story" in t_lower or "behind" in t_lower:
        parts.extend(["real", "story"])

    query_terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = part.strip(" |.,:;!?")
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            query_terms.append(cleaned)

    query = " ".join(query_terms).strip()
    if not query:
        query = topic or "true crime documentary"
    print(f"[RESEARCH] search_query generated: {query}")
    return query


def _build_summary(topic: str, payload: dict) -> str:
    summary = (
        payload.get("summary")
        or payload.get("historical_context")
        or payload.get("what_happened_after")
        or payload.get("real_story")
        or ""
    )
    if isinstance(summary, str) and summary.strip():
        return summary.strip()[:1200]

    facts = (
        payload.get("research_facts")
        or payload.get("real_facts")
        or payload.get("shocking_real_facts")
        or payload.get("research_shocking")
        or []
    )
    if isinstance(facts, list) and facts:
        return " ".join(str(f) for f in facts[:3]).strip()[:1200]
    return f"Research context for {topic}."


def _build_keywords(topic: str, search_query: str, payload: dict, entities: dict) -> list[str]:
    existing = payload.get("keywords") or []
    if isinstance(existing, str):
        existing = [existing]
    keywords: list[str] = []
    for value in list(existing) + (entities.get("named_persons") or []):
        if value and str(value).strip():
            keywords.append(str(value).strip())
    if entities.get("adaptation_title"):
        keywords.append(str(entities["adaptation_title"]).strip())
    keywords.extend(_clean_search_terms(search_query))
    keywords.extend(_clean_search_terms(topic)[:4])

    out: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        key = kw.lower()
        if key not in seen:
            seen.add(key)
            out.append(kw)
        if len(out) >= 10:
            break
    return out or [topic or "documentary"]


def build_canonical_research_payload(
    topic: str | dict,
    research: dict | None = None,
    *,
    manual: bool = False,
    series_name: str | None = None,
    user_note: str | None = None,
) -> dict:
    """
    Return the one canonical research schema used by auto and manual flows.

    Required keys are always present:
    topic, search_query, domain, topic_hash, entities, summary, keywords.
    Existing deep-research fields are preserved.
    """
    topic_obj = topic if isinstance(topic, dict) else {}
    topic_text = (
        (topic_obj.get("topic") if topic_obj else "")
        or (research or {}).get("topic")
        or (research or {}).get("series")
        or str(topic or "")
    ).strip()
    if not topic_text:
        topic_text = "Untitled documentary topic"

    payload = dict(research or {})
    payload["topic"] = topic_text
    if series_name and not payload.get("series_name"):
        payload["series_name"] = series_name
    if user_note and not payload.get("user_discovery"):
        payload["user_discovery"] = user_note

    entities = payload.get("entities")
    if not isinstance(entities, dict) or not entities:
        entities = extract_canonical_entities(topic_text)
    domain = payload.get("domain") or classify_topic_domain_with_context(topic_text, entities)
    entities["domain"] = domain

    search_query = (
        payload.get("search_query")
        or topic_obj.get("search_query")
        or generate_search_query(topic_text, entities)
    )

    payload["search_query"] = str(search_query or topic_text).strip() or topic_text
    payload["domain"] = domain
    payload["topic_hash"] = payload.get("topic_hash") or hashlib.sha256(topic_text.encode("utf-8")).hexdigest()[:16]
    payload["entities"] = entities
    payload["summary"] = _build_summary(topic_text, payload)
    payload["keywords"] = _build_keywords(topic_text, payload["search_query"], {**topic_obj, **payload}, entities)

    print("[RESEARCH] Canonical payload built")
    if manual:
        print("[RESEARCH] Manual topic payload normalized")
    return payload


def repair_research_payload(topic: str | dict, research: dict | None = None, *, manual: bool = False) -> dict:
    """Repair incomplete research payloads instead of letting downstream code crash."""
    current = research or {}
    missing = [key for key in _CANONICAL_RESEARCH_KEYS if not current.get(key)]
    if missing:
        print(f"[ERROR] Missing research field repaired: {', '.join(missing)}")
        return build_canonical_research_payload(topic, current, manual=manual)
    return build_canonical_research_payload(topic, current, manual=manual)


def _is_known_documentary_subject(name: str) -> bool:
    """
    Return True if name is a known documentary subject:
      1. Matched against entity_guard._KNOWN_CRIMINALS (curated list)
      2. Found in any content/<topic>/characters/character_registry.json
    """
    name_l = name.strip().lower()
    try:
        from agent.entity_guard import _KNOWN_CRIMINALS
        for known in _KNOWN_CRIMINALS:
            if known.lower() == name_l or name_l == known.lower().split()[-1]:
                return True
    except ImportError:
        pass

    # Registry scan — catch identities locked in previous runs
    try:
        import glob as _glob
        slug = re.sub(r'\s+', '_', name_l)
        for reg_path in _glob.glob("content/*/characters/character_registry.json"):
            try:
                import json as _json
                with open(reg_path, encoding="utf-8") as _f:
                    reg = _json.load(_f)
                if slug in reg:
                    return True
                for entry in reg.values():
                    if entry.get("canonical_name", "").lower() == name_l:
                        return True
                    for alias in entry.get("aliases", []):
                        if alias.lower() == name_l:
                            return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def semantic_confidence_score(
    topic: str,
    entities: dict,
    wiki_text: str | None = None,
) -> float:
    """
    Score topic integrity 0.0–1.0 before committing to full research.

    HIGH   ≥ 0.7  — proceed normally
    MEDIUM ≥ 0.4  — proceed with caution
    LOW    < 0.4  — weak/uncertain, but pipeline may still continue if
                     entity_confidence_score() is high enough

    Scoring is entity-first: a clean named entity always beats a long
    but structurally weak title.
    """
    score = 0.0
    t = topic.strip()
    words = t.split()

    # ── 0. Registry-aware canonical identity boost ────────────────────────────
    # Short Title Case names and slug-converted identities get an automatic
    # boost when they match a known documentary subject. This prevents canonical
    # IDs like "Jeffrey Epstein" (converted from jeffrey_epstein) from scoring
    # too low due to brevity.
    if 2 <= len(words) <= 5 and _is_known_documentary_subject(t):
        score = 0.92
        print(f"[TOPIC] Registry identity matched: '{t[:60]}' — confidence boosted to 0.92")
        return score

    # 2-4 word pure Title Case name that's not a known list entry still gets
    # a head-start so "Jeffrey Epstein" (slug-converted) isn't penalized.
    _slug_origin = (
        2 <= len(words) <= 4 and
        all(w[0].isupper() for w in words) and
        not words[0].lower() in {"the", "a", "an"}
    )
    if _slug_origin and _is_known_documentary_subject(t):
        score = 0.92
        print(f"[TOPIC] Canonical ID auto-approved: '{t[:60]}'")
        return score
    named_persons = entities.get("named_persons", [])

    # ── 1. Entity quality (primary signal, up to 0.45) ───────────────────────
    # Pure named entity: 2–4 words, first+last are Title Case, first is not
    # an article, connectors ≤3 chars allowed (e.g. "bin", "de", "van").
    _NON_ART = frozenset({"the", "a", "an"})
    _pure_entity = (
        2 <= len(words) <= 4 and
        words[0][0].isupper() and
        words[-1][0].isupper() and
        words[0].lower() not in _NON_ART and
        (named_persons or all(w[0].isupper() or len(w) <= 3 for w in words))
    )

    if _pure_entity:
        score += 0.45
        print(f"[ENTITY] Strong named-person match: '{t[:60]}'")
    elif named_persons:
        score += 0.25  # named persons in a longer/mixed title
    # else: no entity detected — score stays at 0

    # ── 2. Domain specificity (up to 0.20) ───────────────────────────────────
    domain = entities.get("domain", "default")
    if domain in ("biography",):
        score += 0.20  # named-entity domain — same weight as other specifics
        print(f"[DOMAIN] Person-topic classification: biography")
    elif domain != "default":
        score += 0.20
    else:
        score += 0.05

    # ── 3. Era / temporal context (up to 0.10) ───────────────────────────────
    if entities.get("era"):
        score += 0.10

    # ── 4. Event type specificity (up to 0.10) ───────────────────────────────
    if entities.get("event_type", "general") != "general":
        score += 0.10

    # ── 5. Title completeness bonus (up to 0.10) — NOT a penalty for short ───
    if _pure_entity:
        score += 0.05  # entity already validated — not at risk of truncation
    else:
        last_word = words[-1] if words else ""
        if len(last_word) > 4 or last_word.lower() in _VALID_SHORT_TERMINALS:
            score += 0.05

    # Descriptive title (30+ chars) gets a small bonus
    if len(t) >= 30:
        score += 0.05

    # ── 6. Wikipedia alignment bonus (up to 0.10) ────────────────────────────
    if wiki_text:
        _stop = frozenset({
            "with", "from", "that", "this", "have", "were", "been",
            "their", "they", "what", "also", "more", "when",
        })
        t_words = {w for w in re.findall(r"[a-z]{4,}", t.lower())
                   if w not in _stop}
        if t_words:
            w_lower = wiki_text.lower()
            hits = sum(1 for w in t_words if w in w_lower)
            overlap = hits / len(t_words)
            if overlap >= 0.5:
                score += 0.10
                print(f"[ENTITY] Wikipedia biography match ({overlap:.0%} overlap)")
            elif overlap >= 0.25:
                score += 0.05

    score = min(score, 1.0)
    label = "HIGH" if score >= 0.7 else ("MEDIUM" if score >= 0.4 else "LOW")
    print(f"[CONFIDENCE] '{t[:60]}' = {score:.2f} ({label})")
    return score


def entity_confidence_score(topic: str, entities: dict) -> float:
    """
    Score whether the topic resolves to a real, identifiable documentary
    subject — independent of domain certainty.

    Pipeline uses this as a HARD-ABORT gate (threshold: < 0.20):
    - < 0.20 → garbage / malformed / single meaningless word → hard abort
    - ≥ 0.20 → has structure; proceed (may still trigger soft-fallback warning)
    - ≥ 0.55 → strong entity detected

    This is separate from semantic_confidence_score() which also measures
    domain certainty.  A topic can have HIGH entity confidence but LOW
    domain confidence — that is a SAFE CONTINUE, not an abort.
    """
    t = topic.strip()
    words = t.split()
    named_persons = entities.get("named_persons", [])

    # ── Registry-aware canonical boost ───────────────────────────────────────
    # Known documentary subjects always pass the entity gate — regardless of
    # brevity. This covers slug-converted names like "Ted Bundy" (9 chars).
    if 2 <= len(words) <= 5 and _is_known_documentary_subject(t):
        print(f"[TOPIC] Topic auto-approved from registry: '{t[:60]}'")
        return 0.90

    _NON_ART = frozenset({"the", "a", "an"})

    # Pure named entity: 2–4 words, first+last uppercase, connectors allowed
    _pure_entity = (
        2 <= len(words) <= 4 and
        bool(words) and
        words[0][0].isupper() and
        words[-1][0].isupper() and
        words[0].lower() not in _NON_ART and
        (named_persons or all(w[0].isupper() or len(w) <= 3 for w in words))
    )

    if _pure_entity:
        score = 0.75
        print(f"[ENTITY] Valid documentary subject detected: '{t[:60]}'")
    elif named_persons:
        score = 0.60
        print(f"[ENTITY] Named persons found: {named_persons[:2]}")
    elif len(words) >= 5:
        score = 0.40  # long descriptive title — probably meaningful
    elif len(words) >= 3:
        score = 0.25  # short but has some structure
    else:
        score = 0.10  # single word or too short

    if entities.get("era"):
        score = min(score + 0.15, 1.0)

    return min(score, 1.0)


def _validate_wikipedia_relevance(
    wiki_text: str,
    topic: str,
    entities: dict,
) -> bool:
    """
    Return True if the fetched Wikipedia page is semantically relevant.

    Rejects:
    - Disambiguation pages
    - "List of …" pages
    - Pages with <25% keyword overlap with the topic
    - Archaeology topics where wiki has no archaeology vocabulary
    """
    if not wiki_text:
        return False

    header = wiki_text[:300].lower()

    if "disambiguation" in header:
        print("[WIKI VALID] Rejected: disambiguation page")
        return False
    if re.match(r"\s*list of", header):
        print("[WIKI VALID] Rejected: 'List of …' page")
        return False

    _stop = frozenset({
        "with", "from", "that", "this", "have", "were", "been",
        "their", "they", "what", "also", "more", "when", "real",
        "show", "film", "story", "series", "crime",
    })
    t_words = {w for w in re.findall(r"[a-z]{4,}", topic.lower())
               if w not in _stop}

    if not t_words:
        return True  # can't validate — let through

    wiki_full = wiki_text.lower()
    hits = sum(1 for w in t_words if w in wiki_full)
    overlap = hits / len(t_words)

    if overlap < 0.25:
        print(f"[WIKI VALID] Rejected: {overlap:.0%} overlap "
              f"({hits}/{len(t_words)} topic words found)")
        return False

    # Archaeology domain check
    if entities.get("domain") == "archaeology":
        arch_kws = ["archaeolog", "excavat", "ancient", "biblical",
                    "ruins", "tomb", "site", "bronze age"]
        if not any(kw in wiki_full for kw in arch_kws):
            print("[WIKI VALID] Rejected: archaeology topic but no "
                  "archaeology vocabulary in wiki page")
            return False

    print(f"[WIKI VALID] Accepted: {overlap:.0%} overlap "
          f"({hits}/{len(t_words)} topic words found)")
    return True


def _detect_show_topic(topic: str) -> tuple[bool, str | None]:
    """
    Return (is_show_topic, canonical_show_name).

    ONLY returns True when the topic contains EXPLICIT adaptation markers
    (e.g. "series", "movie", "real story behind", "netflix", etc.).
    Keyword-overlap alone — e.g. a show title appearing as part of a different
    phrase — is NOT sufficient.  This prevents topics like
    "The Archaeological Discovery of the Ancient City of Gomorrah" from
    activating the Gomorrah TV-series cast extraction.
    """
    t = topic.lower().strip()

    # Guard: explicit adaptation marker required
    if not any(marker in t for marker in _ADAPTATION_EXPLICIT_MARKERS):
        print(f"[SHOW MODE] disabled — no explicit adaptation reference in topic")
        return False, None

    print(f"[SHOW MODE] explicit adaptation detected")

    # Match against known shows
    for show_key in _KNOWN_SHOW_CHARACTERS:
        if show_key in t:
            return True, show_key
    for show in REAL_STORY_SHOWS:
        if show in t:
            return True, show
    if any(kw in t for kw in _SHOW_TRIGGER_KEYWORDS):
        return True, None
    return True, None


def _fetch_show_cast_from_wikipedia(show_name: str) -> list[dict]:
    """
    Fetch show Wikipedia page and use Groq to extract fictional characters
    and the real people they are based on.
    Returns list of {character, actor, based_on, real_role} dicts.
    """
    # Check hardcoded map first
    show_key = show_name.lower()
    for k, chars in _KNOWN_SHOW_CHARACTERS.items():
        if k in show_key or show_key in k:
            print(f"[Research] Using hardcoded cast map for '{show_name}': {len(chars)} characters")
            return chars

    # Fall back to Wikipedia + Groq extraction
    wiki = fetch_wikipedia(f"{show_name} TV series") or fetch_wikipedia(show_name)
    if not wiki:
        return []

    prompt = f"""Extract the main characters from this Wikipedia article about the TV show/film "{show_name}".
For each main character (up to 6), identify:
1. The fictional character name
2. The actor/actress who plays them
3. The real person they are based on (if any)
4. What that real person actually did in real life

Return ONLY valid JSON in this format:
{{
  "characters": [
    {{
      "character": "Fictional Character Name",
      "actor": "Actor Name",
      "based_on": "Real Person Name or null",
      "real_role": "What the real person actually did"
    }}
  ]
}}

Wikipedia content:
{wiki[:3000]}

Respond with valid JSON only, no markdown."""

    try:
        raw  = _ai_call(prompt, temperature=0.1, max_tokens=1000, json_mode=False)
        data = normalize_ai_json_response(raw, required_keys=["characters"], list_keys=["characters"])
        chars = data.get("characters") or []
        print(f"[Research] Extracted {len(chars)} characters from '{show_name}' Wikipedia")
        return chars
    except Exception as e:
        print(f"[Research] Cast extraction failed for '{show_name}': {e}")
        return []


def is_fictional(topic: str, series_name: str | None = None) -> bool:
    """Return True if the topic appears to be a purely fictional show/character."""
    topic_lower  = topic.lower()
    series_lower = (series_name or "").lower()
    for show in FICTIONAL_SHOWS:
        if show in topic_lower or show in series_lower:
            return True
    return False


def is_real_story(topic: str, series_name: str | None = None) -> bool:
    """Return True if the topic is a known real-story show or person."""
    topic_lower  = topic.lower()
    series_lower = (series_name or "").lower()
    for show in REAL_STORY_SHOWS:
        if show in topic_lower or show in series_lower:
            return True
    return False


# ── Wikipedia fetchers ──────────────────────────────────────

def compress_research_context(wiki_text: str, ddg_dict: dict,
                              max_chars: int = 6000) -> tuple[str, dict]:
    """
    Deduplicate and compress research context before AI calls.

    Removes duplicate lines, low-information snippets, and excess entity
    repetition. Respects a total char budget split proportionally between
    Wikipedia and DuckDuckGo sources.

    Returns (compressed_wiki, compressed_ddg_dict).
    """
    def _compress_block(text: str, budget: int) -> str:
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        seen: set[str] = set()
        unique: list[str] = []
        dup_count = 0
        for line in lines:
            key = line.lower()
            # Skip near-duplicate (exact lower-case match)
            if key in seen:
                dup_count += 1
                continue
            # Skip very short / vague lines
            if len(line) < 40 and not any(c.isdigit() for c in line):
                continue
            seen.add(key)
            unique.append(line)
        if dup_count:
            print(f"[Research] Removed {dup_count} duplicate lines")
        result = "\n".join(unique)
        if len(result) > budget:
            result = result[:budget]
        return result

    n_ddg = max(len(ddg_dict), 1)
    wiki_budget = max_chars // 2
    ddg_budget_total = max_chars - wiki_budget
    per_key_budget = ddg_budget_total // n_ddg

    wiki_compressed = _compress_block(wiki_text or "", wiki_budget)

    ddg_compressed: dict[str, str] = {}
    for key, val in ddg_dict.items():
        ddg_compressed[key] = _compress_block(val or "", per_key_budget)

    orig_chars = len(wiki_text or "") + sum(len(v or "") for v in ddg_dict.values())
    new_chars  = len(wiki_compressed) + sum(len(v) for v in ddg_compressed.values())
    if orig_chars > new_chars:
        print(f"[Research] Compressed {orig_chars // 1024}KB -> {new_chars // 1024}KB")

    return wiki_compressed, ddg_compressed


def fetch_wikipedia(query: str, lang: str = "en") -> str | None:
    """Fetch Wikipedia article content with retry, empty-response guard, and User-Agent."""
    import time as _time

    # Clean query — remove URL fragments and trailing commas
    clean_query = query.split("=")[0].split(",")[0].strip()

    base_url = f"https://{lang}.wikipedia.org/w/api.php"
    headers  = {"User-Agent": "DarkCrimeDecoded/1.0 (abdotajelsir@gmail.com)"}

    for attempt in range(3):
        try:
            search_resp = requests.get(
                base_url,
                params={
                    "action":   "query",
                    "format":   "json",
                    "list":     "search",
                    "srsearch": clean_query,
                    "srlimit":  3,
                    "utf8":     1,
                },
                headers=headers,
                timeout=15,
            )

            if not search_resp.content:
                print(f"[Research] Wikipedia empty response (attempt {attempt + 1})")
                _time.sleep(2)
                continue

            if search_resp.status_code != 200:
                print(f"[Research] Wikipedia status {search_resp.status_code} (attempt {attempt + 1})")
                _time.sleep(2)
                continue

            results = search_resp.json().get("query", {}).get("search", [])
            if not results:
                print(f"[Research] Wikipedia no results for '{clean_query}'")
                return None

            page_title = results[0]["title"]

            content_resp = requests.get(
                base_url,
                params={
                    "action":           "query",
                    "format":           "json",
                    "titles":           page_title,
                    "prop":             "extracts",
                    "explaintext":      True,
                    "exsectionformat":  "plain",
                    "exlimit":          1,
                    "utf8":             1,
                },
                headers=headers,
                timeout=15,
            )

            if not content_resp.content:
                return None

            pages = content_resp.json().get("query", {}).get("pages", {})
            if not pages:
                return None

            content = next(iter(pages.values())).get("extract", "")
            if content:
                print(f"[Research] Wikipedia found: {page_title}")
                return content[:5000]

            return None

        except Exception as e:
            print(f"[Research] Wikipedia attempt {attempt + 1} failed: {e}")
            _time.sleep(3)

    return None


def fetch_wikipedia_arabic(query: str) -> str | None:
    """Fetch Arabic Wikipedia content."""
    base_url = "https://ar.wikipedia.org/w/api.php"

    search_params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srlimit": 1,
    }

    try:
        search_resp = requests.get(base_url, params=search_params, timeout=15)
        search_data = search_resp.json()
        results = search_data["query"]["search"]

        if not results:
            return None

        page_title = results[0]["title"]

        content_params = {
            "action": "query",
            "format": "json",
            "titles": page_title,
            "prop": "extracts",
            "explaintext": True,
        }

        content_resp = requests.get(base_url, params=content_params, timeout=15)
        content_data = content_resp.json()
        pages = content_data["query"]["pages"]
        page = next(iter(pages.values()))

        return page.get("extract", "")[:3000]

    except Exception as e:
        print(f"[Research] Arabic Wikipedia failed: {e}")
        return None


# ── DuckDuckGo search helper (fallback) ────────────────────

def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return concatenated snippet text."""
    try:
        with DDGS(proxy=_ddgs_proxy()) as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return " ".join(r.get("body", "") for r in results)[:3000] or "(no results)"
    except Exception as e:
        return f"(search error: {e})"


# ── Covered topics tracker ──────────────────────────────────

def _load_covered() -> list[dict]:
    if COVERED_TOPICS_PATH.exists():
        try:
            raw = COVERED_TOPICS_PATH.read_text(encoding="utf-8")
            return safe_json_parse(raw, fallback={}).get("covered", [])
        except Exception:
            pass
    return []


def _covered_series_set() -> set[str]:
    return {entry["series"].lower() for entry in _load_covered()}


def mark_covered(series: str, video_id: str) -> None:
    """Call after a successful upload to prevent repeating the topic."""
    COVERED_TOPICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    covered = _load_covered()
    covered.append({
        "series": series,
        "date": datetime.date.today().isoformat(),
        "video_id": video_id,
    })
    COVERED_TOPICS_PATH.write_text(
        json.dumps({"covered": covered}, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"[Research] Marked as covered: {series}")


# ── Series discovery (DuckDuckGo + Groq) ───────────────────

def discover_new_series() -> list[str]:
    """Find fresh crime series not yet covered. Returns up to 20 names."""
    already_done = _covered_series_set()

    queries = [
        "best crime series Netflix 2025 2026",
        "top crime movies based on true story IMDB",
        "new true crime documentary 2026",
        "most watched crime series all time",
        "Arabic crime series true story Middle East documentary",
        "best international crime series based on true events",
    ]

    raw_text = ""
    for q in queries:
        raw_text += f"\nQuery: {q}\n{web_search(q)}\n"
        time.sleep(0.3)

    # Seed with global niches so AI knows what territory to include
    global_seed = "\n".join(f"- {n}" for n in GLOBAL_NICHES)

    prompt = f"""You are a content researcher. Based on the search results below,
compile a list of 30 unique crime TV series or movies (real titles only).
Include all-time classics, recent 2024-2026 releases, and global/Arabic content.

GLOBAL TOPIC SEEDS (include relevant ones):
{global_seed}

Search results:
{raw_text[:4000]}

Return ONLY this JSON:
{{
  "series": ["Title 1", "Title 2", "Title 3", ...]
}}"""

    try:
        data = normalize_ai_json_response(
            _ai_call(prompt, temperature=0.3, max_tokens=1000),
            required_keys=["series"],
            list_keys=["series"],
        )
        all_series = data.get("series") or []
        fresh = [s for s in all_series if s.lower() not in already_done]
        print(f"[Research] Discovered {len(fresh)} fresh series ({len(all_series) - len(fresh)} already covered)")
        # Also inject uncovered global niches directly
        for niche in GLOBAL_NICHES:
            if niche.lower() not in already_done and niche not in fresh:
                fresh.append(niche)
        return fresh[:20]
    except Exception as e:
        print(f"[Research] Series discovery failed: {e}")

    # Fallback to built-in NICHES + GLOBAL_NICHES
    fallback = []
    for niche in list(NICHES) + GLOBAL_NICHES:
        s = niche.split("behind")[-1].strip() if "behind" in niche else niche
        if s.lower() not in already_done:
            fallback.append(s)
    return fallback


# ── Topic selection ─────────────────────────────────────────

def get_trending_topic(series: str, niche: str) -> dict:
    prompt = f"""You are a viral content strategist for true crime YouTube/TikTok channels.

Series: {series}
Niche: {niche}

Suggest ONE highly specific, curiosity-driven topic angle for a 12-minute documentary.
The topic must be about REAL historical facts behind {series}.

Return ONLY this JSON:
{{
  "topic": "Specific real-world topic about {series}",
  "angle": "The shocking or surprising angle that hooks viewers",
  "keywords": ["{series}", "crime", "real story"],
  "search_query": "crime dark night investigation"
}}"""

    _fallback_topic = {
        "topic":        f"The Real Story Behind {series}",
        "angle":        f"What really happened behind {series}",
        "keywords":     [series, "crime", "real story"],
        "search_query": f"{series} real true story",
    }
    try:
        raw    = _ai_call(prompt, temperature=0.9, max_tokens=500)
        result = normalize_ai_json_response(
            raw,
            required_keys=["topic", "angle", "keywords", "search_query"],
            list_keys=["keywords"],
        )
        if not result.get("topic"):
            print(f"[Fallback] Topic generation returned empty — using default topic for {series}")
            result = _fallback_topic
    except Exception as e:
        print(f"[Research] get_trending_topic failed: {e} — using fallback topic")
        result = _fallback_topic
    result["niche"]  = niche
    result["series"] = series
    return result


def research_topics(count: int = 2, niches: list[str] | None = None) -> list[dict]:
    """Discover fresh topics, filter covered, pick best, generate angles.

    Args:
        count:  Number of topics to return.
        niches: Optional explicit niche list (overrides the config NICHES and
                skips the Dark Crime series discovery flow entirely).  Pass
                config_shopmart.NICHES when calling from run_shopmart.py.
    """
    covered = _covered_series_set()

    # ── Shopmart / non-crime path: pick directly from caller-supplied niches ──
    if niches is not None:
        available = [n for n in niches if n.lower() not in covered]
        if not available:
            available = list(niches)          # recycle if all covered
        random.shuffle(available)
        selected_niches = available[:count]
        topics = []
        for niche in selected_niches:
            topic = get_trending_topic(niche, niche)
            topics.append(topic)
            print(f"[Research] Found topic: {topic['topic']} ({niche})")
        return topics

    # ── Dark Crime path: discover series via DuckDuckGo ──────────────────────
    fresh_series = discover_new_series()

    if not fresh_series:
        fresh_series = [
            niche.split("behind")[-1].strip() if "behind" in niche else niche
            for niche in NICHES
            if (niche.split("behind")[-1].strip() if "behind" in niche else niche).lower() not in covered
        ]

    random.shuffle(fresh_series)
    selected = fresh_series[:count]

    if not selected:
        print("[Research] All known series covered — recycling oldest topics")
        all_series = [
            niche.split("behind")[-1].strip() if "behind" in niche else niche
            for niche in NICHES
        ]
        random.shuffle(all_series)
        selected = all_series[:count]

    topics = []
    for series in selected:
        niche = next(
            (n for n in NICHES if series.lower() in n.lower()),
            f"True crime — real story behind {series}"
        )
        topic = get_trending_topic(series, niche)

        # ── Risk classification — filter HIGH-RISK topics in AUTO mode ────────
        try:
            from agents.topic_risk import classify_topic_risk, log_risk
            _risk = classify_topic_risk(topic.get("topic", series), is_manual=False)
            log_risk(topic.get("topic", series), _risk)
            if _risk["manual_confirmation_required"]:
                print(
                    f"[RISK] AUTO-SKIP: '{topic.get('topic', series)}' is HIGH RISK "
                    f"for autonomous generation (signals: {_risk['matched_signals']}). "
                    f"Creator must manually select this topic."
                )
                continue  # skip this topic — do not add to returned list
            topic["risk_info"] = _risk
        except Exception as _re:
            print(f"[RISK] Classification failed (non-fatal): {_re}")

        topics.append(topic)
        print(f"[Research] Found topic: {topic['topic']} ({niche})")

    # If all discovered topics were high-risk, fall back to safe NICHES
    if not topics:
        print("[RISK] All auto-discovered topics were high-risk — falling back to safe NICHES")
        safe_series = [
            niche.split("behind")[-1].strip() if "behind" in niche else niche
            for niche in NICHES
        ]
        random.shuffle(safe_series)
        for series in safe_series[:count]:
            niche = next(
                (n for n in NICHES if series.lower() in n.lower()),
                f"True crime — real story behind {series}"
            )
            topic = get_trending_topic(series, niche)
            topic["risk_info"] = {"risk_level": "LOW", "selection_mode": "AUTO",
                                  "editorial_mode": False, "manual_confirmation_required": False,
                                  "matched_signals": []}
            topics.append(topic)
            print(f"[Research] Safe fallback topic: {topic['topic']} ({niche})")
            if len(topics) >= count:
                break

    return topics


# ── Wikipedia structured extraction ────────────────────────

def extract_from_wikipedia(person_wiki: str | None, series_wiki: str | None = None) -> dict | None:
    """Use Groq to extract structured facts from Wikipedia content.

    Args:
        person_wiki: Wikipedia text about the real person.
        series_wiki: Wikipedia text about the TV series / movie (optional).
    """
    combined = f"PERSON INFO:\n{person_wiki or 'Not found'}\n\n"
    if series_wiki:
        combined += f"SERIES/MOVIE INFO:\n{series_wiki}\n\n"

    prompt = f"""Based ONLY on this Wikipedia content, extract accurate information.
Do NOT add anything not in the Wikipedia text.
Do NOT guess or assume anything.

Wikipedia content:
{combined}

Extract and return JSON:
{{
    "real_person": "full name or null",
    "birth_date": "date if mentioned or null",
    "death_date": "date if mentioned, 'alive' if mentioned as alive, or null",
    "nationality": "country or null",
    "crimes": ["specific crime 1", "specific crime 2"],
    "real_facts": ["verified fact 1", "verified fact 2", "verified fact 3", "verified fact 4", "verified fact 5"],
    "series_name": "exact series/movie name",
    "network": "HBO/Netflix/Amazon/etc - exact from Wikipedia or null",
    "premiere_year": "year or null",
    "what_show_changed": ["verified change 1", "verified change 2", "verified change 3"],
    "shocking_real_facts": ["shocking verified fact 1", "shocking verified fact 2", "shocking verified fact 3"],
    "real_people_in_show": {{"character name": "real person name"}},
    "sources": ["Wikipedia - Person", "Wikipedia - Series"]
}}

Return ONLY valid JSON. If info is not in Wikipedia, use null for strings or [] for arrays."""

    try:
        raw  = _ai_call(prompt, temperature=0.1, max_tokens=2000)
        data = normalize_ai_json_response(
            raw,
            required_keys=["real_person", "birth_date", "death_date", "nationality",
                           "crimes", "real_facts", "series_name", "network",
                           "premiere_year", "what_show_changed", "shocking_real_facts",
                           "real_people_in_show", "sources"],
            list_keys=["crimes", "real_facts", "what_show_changed", "shocking_real_facts", "sources"],
        )
        if not any(data.get(k) for k in ["real_facts", "series_name", "real_person"]):
            print("[Research] Wikipedia extraction: empty/invalid JSON response")
            return None
        return data
    except Exception as e:
        print(f"[Research] Wikipedia extraction failed: {e}")
        return None


# ── Research normalization ─────────────────────────────────

def normalize_research_context(research: dict, topic: str, series: str = "") -> dict:
    """
    Normalize and clean a research dict before script generation.

    Steps applied to research_facts, research_inaccuracies, research_shocking
    (and their legacy-field aliases):
      1. Filter off-topic facts (keyword overlap with topic/series)
      2. Deduplicate near-identical facts (Jaccard ≥ 0.45)
      3. Sort surviving facts by confidence: Wikipedia-corroborated first

    Modifies the dict in-place and returns it.
    Non-list fields and short facts (< 4 words) are left unchanged.
    """
    if not research:
        return research

    try:
        try:
            from agents.script_quality import filter_contaminated_facts
        except ImportError:
            from agent.script_quality import filter_contaminated_facts
    except Exception:
        return research

    _stop = frozenset({
        "the", "and", "was", "that", "had", "for", "with", "his", "her",
        "they", "were", "from", "but", "not", "who", "all", "one", "have",
        "this", "what", "when", "into", "than", "show", "real", "series",
        "film", "movie", "also", "their", "about", "more", "after",
    })

    def _words(t: str) -> set:
        import re as _re
        return {w for w in _re.findall(r"[a-z]+", t.lower())
                if len(w) > 3 and w not in _stop}

    def _jaccard(a: str, b: str) -> float:
        wa, wb = _words(a), _words(b)
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

    def _dedup(facts: list, threshold: float = 0.45) -> list:
        kept: list = []
        for fact in facts:
            if not fact or not isinstance(fact, str) or len(fact.split()) < 4:
                continue
            if not any(_jaccard(fact, p) >= threshold for p in kept):
                kept.append(fact)
        return kept

    def _sort_by_confidence(facts: list, wiki_text: str) -> list:
        """Wikipedia-corroborated facts first; DDG-only last."""
        wiki_words = _words(wiki_text) if wiki_text else set()

        def _score(f: str) -> int:
            hits = len(_words(f) & wiki_words)
            return 3 if hits >= 3 else (2 if hits >= 1 else 1)

        return sorted(facts, key=_score, reverse=True)

    wiki_text     = research.get("real_story", "")
    total_removed = 0

    for key in (
        "research_facts", "research_inaccuracies", "research_shocking",
        "what_show_got_right", "what_show_got_wrong", "shocking_real_facts",
    ):
        raw = research.get(key)
        if not isinstance(raw, list) or not raw:
            continue
        filtered = filter_contaminated_facts(raw, topic, series)
        deduped  = _dedup(filtered)
        sorted_f = _sort_by_confidence(deduped, wiki_text)
        research[key] = sorted_f
        removed = len(raw) - len(sorted_f)
        if removed > 0:
            print(f"[Research] Normalize {key}: {len(raw)} → {len(sorted_f)} "
                  f"({removed} removed)")
            total_removed += removed

    if total_removed:
        print(f"[Research] Normalized: {total_removed} noisy/duplicate entries removed total")

    return research


# ── DuckDuckGo fallback research ───────────────────────────

def research_series_duckduckgo(topic: str) -> dict:
    """Fallback: search DuckDuckGo then use Groq to extract structured facts."""
    print(f"[Research] DuckDuckGo fallback for: {topic}")

    raw_facts       = web_search(f"{topic} real true story historical facts biography", 5)
    raw_inspiration = web_search(f"{topic} true story inspiration how show adapted real events", 3)
    raw_shocking    = web_search(f"{topic} shocking facts untold story documentary", 3)

    prompt = f"""You are a true crime documentary researcher.
Based on the search results below about "{topic}", extract verified facts.
Use educational, celebratory tone — not accusatory.

Facts about the real story:
{raw_facts[:2500]}

How the show was inspired by real events:
{raw_inspiration[:1500]}

Shocking real details:
{raw_shocking[:1500]}

Return ONLY this JSON:
{{
  "research_facts": [
    "Specific confirmed fact 1 with real dates/names",
    "Specific confirmed fact 2 with real dates/names",
    "Specific confirmed fact 3 with real dates/names",
    "Specific confirmed fact 4 with real dates/names",
    "Specific confirmed fact 5 with real dates/names"
  ],
  "research_inaccuracies": [
    "How real event 1 inspired a scene or character in the show",
    "How real event 2 inspired a scene or character in the show",
    "How real event 3 inspired a scene or character in the show"
  ],
  "research_shocking": [
    "Fascinating real fact that makes the story even more incredible #1",
    "Fascinating real fact that makes the story even more incredible #2",
    "Fascinating real fact that makes the story even more incredible #3"
  ]
}}"""

    try:
        data         = normalize_ai_json_response(
            _ai_call(prompt, temperature=0.2, max_tokens=800),
            required_keys=["research_facts", "research_inaccuracies", "research_shocking"],
            list_keys=["research_facts", "research_inaccuracies", "research_shocking"],
        )
        facts_out    = data.get("research_facts", [])
        wrong_out    = data.get("research_inaccuracies", [])
        shocking_out = data.get("research_shocking", [])
        print(f"[Research] DuckDuckGo: {len(facts_out)} facts, {len(wrong_out)} inspired-by, {len(shocking_out)} shocking")
    except Exception as e:
        print(f"[Research] AI extraction failed: {e} — using raw snippets")
        facts_out    = [raw_facts[:400]]       if raw_facts       else []
        wrong_out    = [raw_inspiration[:400]] if raw_inspiration else []
        shocking_out = [raw_shocking[:400]]    if raw_shocking    else []

    # Filter out facts that have no keyword overlap with the topic
    try:
        from agents.script_quality import filter_contaminated_facts
        facts_out    = filter_contaminated_facts(facts_out,    topic)
        wrong_out    = filter_contaminated_facts(wrong_out,    topic)
        shocking_out = filter_contaminated_facts(shocking_out, topic)
    except Exception as _fe:
        print(f"[Research] Contamination filter error (non-fatal): {_fe}")

    return {
        "series":                        topic,
        "research_facts":                facts_out,
        "research_inaccuracies":         wrong_out,
        "research_shocking":             shocking_out,
        # Legacy fields for backward compatibility
        "real_story":                    raw_facts,
        "what_show_got_right":           facts_out[:3],
        "what_show_got_wrong":           wrong_out,
        "shocking_real_facts":           shocking_out,
        "real_people_behind_characters": {},
    }


# ── Real vs Fiction extractor ───────────────────────────────

def extract_real_vs_fiction(topic: str, research_text: str) -> dict:
    """
    Analyse research text and extract structured real-people / fictional-characters
    mapping plus show-vs-reality comparisons.

    Works for ANY topic — crime docs, biopics, historical shows, sports, etc.
    Returns a dict ready to be merged into script_data.
    """
    prompt = f"""Analyze this research about "{topic}".

Extract the following and respond with valid JSON only, no markdown:

1. Is this based on a true story?
2. Who are the REAL people involved and what did they actually do?
3. If it is a TV show or film, who are the fictional characters and which real person is each one based on?
4. What did the show get right vs what did it change or dramatize?
5. What time period and real locations?

Research text:
{research_text[:3000]}

Return exactly this JSON structure:
{{
  "is_based_on_true_story": true,
  "real_people": [
    {{"name": "Real Person Name", "role": "what they actually did", "era": "time period"}}
  ],
  "fictional_characters": [
    {{"name": "Character Name", "played_by": "Actor Name", "based_on": "Real Person Name", "show": "Show/Film Title"}}
  ],
  "real_vs_show": [
    {{"aspect": "topic area", "reality": "what really happened", "show": "how show depicted it"}}
  ],
  "time_period": "e.g. 1970s-1980s",
  "real_locations": ["Location 1", "Location 2"]
}}

Respond with valid JSON only, no markdown, no explanation."""

    try:
        raw  = _ai_call(prompt, temperature=0.1, max_tokens=1500, json_mode=False)
        data = normalize_ai_json_response(
            raw,
            required_keys=["is_based_on_true_story", "real_people", "fictional_characters",
                           "real_vs_show", "time_period", "real_locations"],
            list_keys=["real_people", "fictional_characters", "real_vs_show", "real_locations"],
        )
        rp  = data.get("real_people", [])
        fc  = data.get("fictional_characters", [])
        rvs = data.get("real_vs_show", [])
        print(f"[Research] real_vs_fiction: {len(rp)} real people, {len(fc)} characters, {len(rvs)} comparisons")
        if rp or fc or rvs:
            return data
        print("[Research] extract_real_vs_fiction: empty data — using default structure")
    except Exception as e:
        print(f"[Research] extract_real_vs_fiction failed: {e}")

    return {
        "is_based_on_true_story": True,
        "real_people": [],
        "fictional_characters": [],
        "real_vs_show": [],
        "time_period": "",
        "real_locations": [],
    }


# ── Deep research on a specific series ─────────────────────

def research_series(topic: str, series_name: str | None = None, user_note: str | None = None) -> dict | None:
    """Combine Wikipedia (primary) + DuckDuckGo (additional) via Groq extraction.

    Args:
        topic:       The real person or subject (e.g. "Pablo Escobar").
        series_name: The TV series or movie title (e.g. "Narcos"). Optional.
        user_note:   Raw text from the channel host (e.g. "Al Capone inspired Nucky
                     Thompson in Boardwalk Empire"). Used as extra research seed.

    Returns None if the topic is detected as a purely fictional show/character.
    """
    # ── Fictional show guard ────────────────────────────────
    if is_fictional(topic, series_name):
        print(f"[Research] WARNING: '{topic}' appears to be fictional — aborting")
        try:
            from agent.notify_agent import send_message as _sm
        except ImportError:
            try:
                from agents.notify_agent import send_message as _sm
            except ImportError:
                _sm = lambda msg: None
        _sm(
            f"\u26a0\ufe0f WARNING: Fictional Content Detected\n\n"
            f'"{topic}" appears to be a fictional character/story.\n'
            f"Dark Crime Decoded covers REAL true crime stories only.\n\n"
            f"Options:\n"
            f"1. Send a REAL person's name instead\n"
            f"2. Send the real inspiration behind the show\n\n"
            f"Real story shows we cover:\n"
            f"- Narcos \u2192 Pablo Escobar (real)\n"
            f"- American Gangster \u2192 Frank Lucas (real)\n"
            f"- Boardwalk Empire \u2192 Nucky Johnson (real)\n"
            f"- Goodfellas \u2192 Henry Hill (real)\n\n"
            f"Send a new topic to continue."
        )
        return None

    print(f"[Research] Starting research: {topic}")
    if user_note:
        print(f"[Research] User note: {user_note[:100]}")

    # ── Domain lock: classify semantic domain BEFORE any show detection ────────
    _topic_domain = classify_topic_domain(topic)
    print(f"[DOMAIN LOCK] active — domain: {_topic_domain}")

    # ── STEP 0: Detect if topic is a TV show and extract cast ─
    # _detect_show_topic now requires explicit adaptation markers, so
    # non-adaptation domains (archaeology, war_historical, etc.) return False
    # here and skip cast extraction entirely.
    _is_show, _show_key = _detect_show_topic(topic)
    _effective_show = series_name or (_show_key and _show_key.title()) or None
    show_characters: list[dict] = []

    if _is_show and _effective_show:
        print(f"[Research] TV show detected: '{_effective_show}' — fetching cast")
        show_characters = _fetch_show_cast_from_wikipedia(_effective_show)
        # Also search Wikipedia for each real person behind the characters
        _real_person_wikis: list[str] = []
        for char in show_characters[:4]:
            real = char.get("based_on") or ""
            if real and real.lower() not in ("null", "none", "various", "composite"):
                rw = fetch_wikipedia(real)
                if rw:
                    _real_person_wikis.append(f"=== {real} ===\n{rw[:1000]}")
                    print(f"[Research] Fetched Wikipedia for real person: {real}")
        _real_people_combined = "\n\n".join(_real_person_wikis)
    else:
        _real_people_combined = ""

    # ── STEP 1: Wikipedia (accurate facts) ─────────────────
    person_wiki = fetch_wikipedia(topic)
    series_wiki = fetch_wikipedia(f"{series_name} TV series") if series_name else None
    # Supplement series_wiki with show Wikipedia if fetched above
    if not series_wiki and _is_show and _effective_show:
        series_wiki = fetch_wikipedia(_effective_show)
    print(f"[Research] Wikipedia: {'found' if person_wiki else 'not found'}")

    # ── STEP 1b: Validate Wikipedia relevance ─────────────────
    _wiki_entities = extract_canonical_entities(topic)
    if person_wiki and not _validate_wikipedia_relevance(person_wiki, topic, _wiki_entities):
        print(f"[Research] Wikipedia page irrelevant — trying alternative query")
        _alt = fetch_wikipedia(f"{topic} historical true story")
        if _alt and _validate_wikipedia_relevance(_alt, topic, _wiki_entities):
            person_wiki = _alt
            print("[Research] Alternative Wikipedia page accepted")
        else:
            person_wiki = None
            print("[Research] Wikipedia discarded — proceeding with DuckDuckGo only")

    # ── STEP 2: DuckDuckGo (additional details) ────────────
    try:
        with DDGS(proxy=_ddgs_proxy()) as ddgs:
            ddg_real = list(ddgs.text(
                f"{topic} real true story historical facts biography",
                max_results=5
            ))
            ddg_inspiration = list(ddgs.text(
                f"{series_name or topic} true story inspiration real events",
                max_results=3
            ))
            ddg_shocking = list(ddgs.text(
                f"{topic} shocking facts untold story documentary",
                max_results=3
            ))
            ddg_real_life = list(ddgs.text(
                f"{topic} what really happened real life story",
                max_results=3
            ))
            # If the host gave a specific connection, search that too
            if user_note:
                ddg_note = list(ddgs.text(user_note[:100], max_results=3))
            else:
                ddg_note = []
        ddg_combined = {
            "real_story":   " ".join(r.get("body", "") for r in ddg_real),
            "inspiration":  " ".join(r.get("body", "") for r in ddg_inspiration),
            "shocking":     " ".join(r.get("body", "") for r in ddg_shocking),
            "real_life":    " ".join(r.get("body", "") for r in ddg_real_life),
            "user_note":    " ".join(r.get("body", "") for r in ddg_note),
        }
        print(f"[Research] DuckDuckGo: {len(ddg_real)} results found")
    except Exception as e:
        print(f"[Research] DuckDuckGo failed: {e}")
        ddg_combined = {"real_story": "", "inspiration": "", "shocking": "", "real_life": "", "user_note": ""}

    if not person_wiki and not series_wiki and not any(ddg_combined.values()):
        print(f"[Research] All sources failed — using DuckDuckGo fallback")
        return research_series_duckduckgo(topic)

    # ── STEP 3: Combine both sources with Groq ──────────────
    user_note_section = ""
    if user_note:
        user_note_section = f"""
HOST DISCOVERY (research this specific connection deeper):
"{user_note}"

ADDITIONAL RESEARCH ON HOST DISCOVERY:
{ddg_combined['user_note'][:800]}
"""

    # Build show characters context block for the Groq prompt
    _show_cast_section = ""
    if show_characters:
        lines = [
            f"  - {c['character']} ({c.get('actor','?')}) → based on {c.get('based_on','?')}: {c.get('real_role','')}"
            for c in show_characters
        ]
        _show_cast_section = (
            "\nSHOW CHARACTERS AND REAL COUNTERPARTS (cover ALL of them):\n"
            + "\n".join(lines) + "\n"
        )
    if _real_people_combined:
        _show_cast_section += f"\nREAL PEOPLE WIKIPEDIA PAGES:\n{_real_people_combined[:2000]}\n"

    # Compress research context before building the prompt — deduplicates lines,
    # removes noise, and enforces a 6 KB total budget to reduce token waste.
    _wiki_combined = f"{(person_wiki or '')}\\n{(series_wiki or '')}"
    _wiki_c, _ddg_c = compress_research_context(_wiki_combined, ddg_combined, max_chars=6000)

    prompt = f"""You are a true crime documentary researcher.
Combine Wikipedia facts with web research to create accurate research data.
The goal is to tell the REAL story that inspired {series_name or topic}.
Not to criticize the show — it is great entertainment. But the real story is
even more fascinating and needs to be told.
{user_note_section}{_show_cast_section}
WIKIPEDIA (primary - most accurate):
{_wiki_c[:3500]}

DUCKDUCKGO (additional details):
Real story: {_ddg_c.get('real_story', '')[:900]}
Inspiration: {_ddg_c.get('inspiration', '')[:700]}
Shocking facts: {_ddg_c.get('shocking', '')[:700]}
Real life events: {_ddg_c.get('real_life', '')[:700]}

RULES:
1. Wikipedia facts take priority over DuckDuckGo
2. Only include facts you are confident are accurate
3. If DuckDuckGo contradicts Wikipedia — use Wikipedia
4. Network/channel info MUST come from Wikipedia only
5. Dates and names MUST come from Wikipedia only
6. Use educational, celebratory tone — not accusatory
7. If a HOST DISCOVERY is given above, make it the central angle of the research

Extract and return JSON:
{{
    "real_person": "full name from Wikipedia",
    "birth_date": "from Wikipedia or null",
    "death_date": "from Wikipedia or null",
    "nationality": "from Wikipedia or null",
    "network": "exact network from Wikipedia - HBO/Netflix/etc or null",
    "premiere_year": "from Wikipedia or null",
    "series_name": "exact name from Wikipedia or null",
    "series_type": "Movie or Series or Documentary — based on Wikipedia content",
    "user_discovery": "{user_note or ''}",
    "user_discovery_expanded": [
        "deeper fact about the host's discovery",
        "more connections found via research",
        "historical context that validates or extends the discovery"
    ],
    "real_facts": [
        "verified fact 1 with date/number",
        "verified fact 2 with date/number",
        "verified fact 3 with date/number",
        "verified fact 4 with date/number",
        "verified fact 5 with date/number"
    ],
    "how_show_inspired": [
        "how real event 1 inspired a scene or character in the show",
        "how real event 2 inspired a scene or character in the show",
        "how real event 3 inspired a scene or character in the show"
    ],
    "shocking_real_facts": [
        "fascinating verified fact 1 that makes story more incredible",
        "fascinating verified fact 2 that makes story more incredible",
        "fascinating verified fact 3 that makes story more incredible",
        "fascinating verified fact 4 that makes story more incredible"
    ],
    "what_happened_after": "what happened in real life after show timeline",
    "real_people_in_show": {{"character": "real person"}},
    "historical_context": "brief historical background"
}}

Return ONLY valid JSON."""

    try:
        raw  = _ai_call(prompt, temperature=0.1, max_tokens=2000)
        info = normalize_ai_json_response(
            raw,
            required_keys=["real_person", "birth_date", "death_date", "nationality",
                           "network", "premiere_year", "series_name", "series_type",
                           "real_facts", "how_show_inspired", "shocking_real_facts",
                           "what_happened_after", "real_people_in_show", "historical_context"],
            list_keys=["real_facts", "how_show_inspired", "shocking_real_facts",
                       "user_discovery_expanded"],
        )
        if not any(info.get(k) for k in ["real_facts", "series_name", "real_person"]):
            print("[Fallback] Combined extraction returned empty — using DuckDuckGo fallback")
            return research_series_duckduckgo(topic)
        print(f"[Research] Combined research complete: {topic}")
        print(f"[Research] Network: {info.get('network', 'unknown')}")
    except Exception as e:
        print(f"[Research] Combined extraction failed: {e} — using DuckDuckGo fallback")
        return research_series_duckduckgo(topic)

    # ── STEP 4: Extract real vs fiction structured data ─────────
    _rvf_text = f"{person_wiki or ''}\n{series_wiki or ''}\n{ddg_combined.get('real_story', '')}"
    real_vs_fiction = extract_real_vs_fiction(topic, _rvf_text)
    print(f"[Research] Real vs fiction: {len(real_vs_fiction.get('real_people', []))} real people, "
          f"{len(real_vs_fiction.get('fictional_characters', []))} fictional chars")

    facts_out    = info.get("real_facts") or []
    inspired_out = info.get("how_show_inspired") or []
    shocking_out = info.get("shocking_real_facts") or []

    # Supplement with DuckDuckGo fallback if Wikipedia gave very thin results
    if len(facts_out) < 3:
        print(f"[Research] Thin results ({len(facts_out)} facts) — supplementing with DuckDuckGo fallback")
        ddg = research_series_duckduckgo(topic)
        facts_out    = facts_out    or ddg["research_facts"]
        inspired_out = inspired_out or ddg["research_inaccuracies"]
        shocking_out = shocking_out or ddg["research_shocking"]

    result_dict = {
        "series":                        series_name or topic,
        # Primary fields used by the script prompt
        "research_facts":                facts_out,
        "research_inaccuracies":         inspired_out,   # "HOW HISTORY INSPIRED THE SHOW"
        "research_shocking":             shocking_out,
        # Host discovery — central hook when user sent a research note
        "user_discovery":                info.get("user_discovery") or user_note or "",
        "user_discovery_expanded":       info.get("user_discovery_expanded") or [],
        # Structured data passed through to script_agent
        "network":                       info.get("network"),
        "premiere_year":                 info.get("premiere_year"),
        "series_type":                   info.get("series_type"),
        "real_person":                   info.get("real_person"),
        "what_happened_after":           info.get("what_happened_after"),
        "historical_context":            info.get("historical_context"),
        # Legacy fields for backward compatibility
        "real_story":                    person_wiki or "",
        "what_show_got_right":           facts_out[:3],
        "what_show_got_wrong":           inspired_out,
        "shocking_real_facts":           shocking_out,
        "real_people_behind_characters": info.get("real_people_in_show", {}),
        # TV show cast: fictional characters + real counterparts (populated for show topics)
        "show_characters": show_characters,
        "is_show_topic":   _is_show,
        # Real vs fiction structured data for script_agent
        "real_vs_fiction": real_vs_fiction,
        # Full structured block
        "wiki": {
            "real_person":         info.get("real_person"),
            "birth_date":          info.get("birth_date"),
            "death_date":          info.get("death_date"),
            "nationality":         info.get("nationality"),
            "network":             info.get("network"),
            "premiere_year":       info.get("premiere_year"),
            "series_name":         info.get("series_name"),
            "series_type":         info.get("series_type"),
            "real_people_in_show": info.get("real_people_in_show", {}),
            "what_happened_after": info.get("what_happened_after"),
            "historical_context":  info.get("historical_context"),
        },
    }
    # ── Normalize research: deduplicate, filter off-topic, sort by confidence ─
    normalize_research_context(result_dict, topic, series_name or "")
    return result_dict
