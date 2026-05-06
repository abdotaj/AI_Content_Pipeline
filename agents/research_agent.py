# ============================================================
#  agents/research_agent.py
#  Wikipedia (primary) + DuckDuckGo (fallback) + Groq
# ============================================================
import random
import json
import time
import datetime
import requests
from pathlib import Path

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

import groq as groq_lib
from groq import Groq
import os
from config import GROQ_API_KEY, NICHES, NICHE_WEIGHTS
from agents.json_utils import safe_json_parse, is_valid_json_response, strip_markdown_fences

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


def _detect_show_topic(topic: str) -> tuple[bool, str | None]:
    """
    Return (is_show_topic, canonical_show_name).
    True when topic is a known show name or contains streaming/TV keywords.
    """
    t = topic.lower().strip()
    # Exact match against known shows
    for show_key in _KNOWN_SHOW_CHARACTERS:
        if show_key in t:
            return True, show_key
    # Check REAL_STORY_SHOWS list
    for show in REAL_STORY_SHOWS:
        if show in t:
            return True, show
    # Keyword triggers
    if any(kw in t for kw in _SHOW_TRIGGER_KEYWORDS):
        return True, None
    return False, None


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
        data = safe_json_parse(raw, fallback={})
        chars = data.get("characters", [])
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
        with DDGS() as ddgs:
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
        data = safe_json_parse(_ai_call(prompt, temperature=0.3, max_tokens=1000),
                               fallback={"series": []})
        all_series = data.get("series", [])
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
        result = safe_json_parse(raw, fallback=_fallback_topic)
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
        topics.append(topic)
        print(f"[Research] Found topic: {topic['topic']} ({niche})")

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
        data = safe_json_parse(raw, fallback=None)
        if not data:
            print("[Research] Wikipedia extraction: empty/invalid JSON response")
        return data
    except Exception as e:
        print(f"[Research] Wikipedia extraction failed: {e}")
        return None


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
        data         = safe_json_parse(_ai_call(prompt, temperature=0.2, max_tokens=800),
                                       fallback={})
        facts_out    = data.get("research_facts", [])
        wrong_out    = data.get("research_inaccuracies", [])
        shocking_out = data.get("research_shocking", [])
        print(f"[Research] DuckDuckGo: {len(facts_out)} facts, {len(wrong_out)} inspired-by, {len(shocking_out)} shocking")
    except Exception as e:
        print(f"[Research] AI extraction failed: {e} — using raw snippets")
        facts_out    = [raw_facts[:400]]       if raw_facts       else []
        wrong_out    = [raw_inspiration[:400]] if raw_inspiration else []
        shocking_out = [raw_shocking[:400]]    if raw_shocking    else []

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
        data = safe_json_parse(raw, fallback={})
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

    # ── STEP 0: Detect if topic is a TV show and extract cast ─
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

    # ── STEP 2: DuckDuckGo (additional details) ────────────
    try:
        with DDGS() as ddgs:
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

    prompt = f"""You are a true crime documentary researcher.
Combine Wikipedia facts with web research to create accurate research data.
The goal is to tell the REAL story that inspired {series_name or topic}.
Not to criticize the show — it is great entertainment. But the real story is
even more fascinating and needs to be told.
{user_note_section}{_show_cast_section}
WIKIPEDIA (primary - most accurate):
Person: {(person_wiki or "Not found")[:2000]}
Series: {(series_wiki or "Not found")[:1500]}

DUCKDUCKGO (additional details):
Real story: {ddg_combined['real_story'][:1000]}
Inspiration: {ddg_combined['inspiration'][:800]}
Shocking facts: {ddg_combined['shocking'][:800]}
Real life events: {ddg_combined['real_life'][:800]}

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
        info = safe_json_parse(raw, fallback=None)
        if not info:
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

    return {
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
