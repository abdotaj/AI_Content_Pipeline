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
from openai import OpenAI
import os
from config import GROQ_API_KEY, NICHES, NICHE_WEIGHTS

_groq   = Groq(api_key=GROQ_API_KEY)
_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",   # primary
    "llama-3.1-8b-instant",      # fallback
]


def _groq_call(**kwargs):
    """Try each model with one 40-second retry on rate limit before moving to fallback."""
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
    raise last_err


def openai_research_call(prompt: str) -> str | None:
    """Use OpenAI gpt-4o-mini for research extraction. Returns raw content string or None."""
    try:
        response = _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a true crime documentary researcher. Extract accurate information from Wikipedia and web sources. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[Research] OpenAI call failed: {e}")
        return None


def _check_openai_connectivity() -> bool:
    """Quick TCP check to api.openai.com:443 before attempting API call."""
    import socket
    try:
        socket.setdefaulttimeout(10)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("api.openai.com", 443))
        return True
    except Exception as e:
        print(f"[Research] OpenAI unreachable: {e}")
        return False


def _ai_call(prompt: str, temperature: float = 0.3,
             max_tokens: int = 1000, json_mode: bool = True) -> str:
    """OpenAI gpt-4o-mini first (connectivity check + 3 retries), fall back to Groq."""
    import os
    import time

    # ── Priority 1: OpenAI ────────────────────────────────────────────────────
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        if not _check_openai_connectivity():
            print("[Research] Skipping OpenAI — not reachable")
        else:
            from openai import OpenAI
            for attempt in range(3):
                try:
                    client = OpenAI(
                        api_key=openai_key,
                        timeout=120.0,
                        max_retries=3,
                    )
                    kwargs = {
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": "You are a true crime documentary researcher. Return accurate information only."},
                            {"role": "user",   "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    response = client.chat.completions.create(**kwargs)
                    result = response.choices[0].message.content
                    print(f"[Research] OpenAI call success ✅ (attempt {attempt + 1})")
                    return result
                except Exception as e:
                    print(f"[Research] OpenAI attempt {attempt + 1} failed: {e}")
                    if attempt < 2:
                        wait = (attempt + 1) * 10
                        print(f"[Research] Waiting {wait}s...")
                        time.sleep(wait)
            print("[Research] OpenAI all attempts failed")

    # ── Priority 2: Groq fallback ────────────────────────────────────────────
    print("[Research] Falling back to Groq")
    max_chars = 3000
    if len(prompt) > max_chars:
        half    = max_chars // 2
        _prompt = prompt[:half] + "\n...\n" + prompt[-half:]
        print(f"[Research] Prompt truncated to {max_chars} chars for Groq")
    else:
        _prompt = prompt
    kwargs = dict(
        messages=[{"role": "user", "content": _prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return _groq_call(**kwargs).choices[0].message.content


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
]


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
            return json.loads(
                COVERED_TOPICS_PATH.read_text(encoding="utf-8")
            ).get("covered", [])
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
    ]

    raw_text = ""
    for q in queries:
        raw_text += f"\nQuery: {q}\n{web_search(q)}\n"
        time.sleep(0.3)

    prompt = f"""You are a content researcher. Based on the search results below,
compile a list of 30 unique crime TV series or movies (real titles only).
Include all-time classics and recent 2024-2026 releases.

Search results:
{raw_text[:4000]}

Return ONLY this JSON:
{{
  "series": ["Title 1", "Title 2", "Title 3", ...]
}}"""

    try:
        data = json.loads(_ai_call(prompt, temperature=0.3, max_tokens=1000))
        all_series = data.get("series", [])
        fresh = [s for s in all_series if s.lower() not in already_done]
        print(f"[Research] Discovered {len(fresh)} fresh series ({len(all_series) - len(fresh)} already covered)")
        return fresh[:20]
    except Exception as e:
        print(f"[Research] Series discovery failed: {e}")

    # Fallback to built-in NICHES
    fallback = []
    for niche in NICHES:
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

    result = json.loads(_ai_call(prompt, temperature=0.9, max_tokens=500))
    result["niche"] = niche
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
        return json.loads(_ai_call(prompt, temperature=0.1, max_tokens=2000))
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
        data = json.loads(_ai_call(prompt, temperature=0.2, max_tokens=800))
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

    # ── STEP 1: Wikipedia (accurate facts) ─────────────────
    person_wiki = fetch_wikipedia(topic)
    series_wiki = fetch_wikipedia(f"{series_name} TV series") if series_name else None
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

    prompt = f"""You are a true crime documentary researcher.
Combine Wikipedia facts with web research to create accurate research data.
The goal is to tell the REAL story that inspired {series_name or topic}.
Not to criticize the show — it is great entertainment. But the real story is
even more fascinating and needs to be told.
{user_note_section}
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
        info = json.loads(_ai_call(prompt, temperature=0.1, max_tokens=2000))
        print(f"[Research] Combined research complete: {topic}")
        print(f"[Research] Network: {info.get('network', 'unknown')}")
    except Exception as e:
        print(f"[Research] Combined extraction failed: {e} — using DuckDuckGo fallback")
        return research_series_duckduckgo(topic)

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
