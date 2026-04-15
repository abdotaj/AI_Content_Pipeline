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
from config import GROQ_API_KEY, NICHES, NICHE_WEIGHTS

_groq = Groq(api_key=GROQ_API_KEY)

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


COVERED_TOPICS_PATH = Path("output/covered_topics.json")


# ── Wikipedia fetchers ──────────────────────────────────────

def fetch_wikipedia(query: str, lang: str = "en") -> str | None:
    """Fetch Wikipedia article content (English by default)."""
    base_url = f"https://{lang}.wikipedia.org/w/api.php"

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
            print(f"[Research] Wikipedia: no results for '{query}'")
            return None

        page_title = results[0]["title"]

        content_params = {
            "action": "query",
            "format": "json",
            "titles": page_title,
            "prop": "extracts",
            "explaintext": True,
            "exsectionformat": "plain",
        }

        content_resp = requests.get(base_url, params=content_params, timeout=15)
        content_data = content_resp.json()
        pages = content_data["query"]["pages"]
        page = next(iter(pages.values()))

        content = page.get("extract", "")
        if not content:
            return None

        print(f"[Research] Wikipedia found: {page_title}")
        return content[:5000]

    except Exception as e:
        print(f"[Research] Wikipedia fetch failed: {e}")
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
        response = _groq_call(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content.strip())
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

    response = _groq_call(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=500,
        response_format={"type": "json_object"}
    )
    result = json.loads(response.choices[0].message.content.strip())
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
        response = _groq_call(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[Research] Wikipedia extraction failed: {e}")
        return None


# ── DuckDuckGo fallback research ───────────────────────────

def research_series_duckduckgo(topic: str) -> dict:
    """Fallback: search DuckDuckGo then use Groq to extract structured facts."""
    print(f"[Research] DuckDuckGo fallback for: {topic}")

    raw_facts    = web_search(f"{topic} real true story historical facts", 5)
    raw_wrong    = web_search(f"{topic} what show got wrong inaccurate dramatized", 3)
    raw_shocking = web_search(f"{topic} most shocking real facts untold story", 3)

    prompt = f"""You are a fact-checker for a true crime documentary channel.
Based on the search results below about "{topic}", extract verified facts.

Facts about the real story:
{raw_facts[:2500]}

What the show got wrong:
{raw_wrong[:1500]}

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
    "What the show got wrong or dramatized #1",
    "What the show got wrong or dramatized #2",
    "What the show got wrong or dramatized #3"
  ],
  "research_shocking": [
    "Most shocking real fact viewers don't know #1",
    "Most shocking real fact viewers don't know #2",
    "Most shocking real fact viewers don't know #3"
  ]
}}"""

    try:
        response = _groq_call(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content.strip())
        facts_out    = data.get("research_facts", [])
        wrong_out    = data.get("research_inaccuracies", [])
        shocking_out = data.get("research_shocking", [])
        print(f"[Research] DuckDuckGo: {len(facts_out)} facts, {len(wrong_out)} inaccuracies, {len(shocking_out)} shocking")
    except Exception as e:
        print(f"[Research] Groq extraction failed: {e} — using raw snippets")
        facts_out    = [raw_facts[:400]]    if raw_facts    else []
        wrong_out    = [raw_wrong[:400]]    if raw_wrong    else []
        shocking_out = [raw_shocking[:400]] if raw_shocking else []

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

def research_series(topic: str, series_name: str | None = None) -> dict:
    """Fetch Wikipedia (primary) then DuckDuckGo (fallback) and extract structured facts.

    Args:
        topic:       The real person or subject (e.g. "Pablo Escobar").
        series_name: The TV series or movie title (e.g. "Narcos"). Optional.
    """
    print(f"[Research] Fetching Wikipedia for: {topic}")

    # ── STEP 1: Wikipedia fetch ─────────────────────────────
    person_wiki = fetch_wikipedia(topic)

    series_wiki = None
    if series_name:
        series_wiki = fetch_wikipedia(f"{series_name} TV series")

    if not person_wiki and not series_wiki:
        print(f"[Research] Wikipedia not found — using DuckDuckGo fallback")
        return research_series_duckduckgo(topic)

    # ── STEP 2: Extract structured data from Wikipedia ──────
    info = extract_from_wikipedia(person_wiki, series_wiki)

    if not info:
        print(f"[Research] Wikipedia extraction returned nothing — using DuckDuckGo fallback")
        return research_series_duckduckgo(topic)

    print(f"[Research] Wikipedia research complete: {topic}")
    print(f"[Research] Network: {info.get('network', 'unknown')}")
    print(f"[Research] Series: {info.get('series_name', series_name or 'unknown')}")

    # ── STEP 3: Map to standard result shape ────────────────
    facts_out    = info.get("real_facts") or []
    wrong_out    = info.get("what_show_changed") or []
    shocking_out = info.get("shocking_real_facts") or []

    # If Wikipedia gave us very thin facts, supplement with DuckDuckGo
    if len(facts_out) < 3:
        print(f"[Research] Wikipedia thin ({len(facts_out)} facts) — supplementing with DuckDuckGo")
        ddg = research_series_duckduckgo(topic)
        facts_out    = facts_out    or ddg["research_facts"]
        wrong_out    = wrong_out    or ddg["research_inaccuracies"]
        shocking_out = shocking_out or ddg["research_shocking"]

    return {
        "series":                        series_name or topic,
        # Primary fields used by the script prompt
        "research_facts":                facts_out,
        "research_inaccuracies":         wrong_out,
        "research_shocking":             shocking_out,
        # Structured Wikipedia data passed through to script_agent
        "network":                       info.get("network"),
        "premiere_year":                 info.get("premiere_year"),
        "real_person":                   info.get("real_person"),
        # Legacy fields for backward compatibility
        "real_story":                    person_wiki or "",
        "what_show_got_right":           facts_out[:3],
        "what_show_got_wrong":           wrong_out,
        "shocking_real_facts":           shocking_out,
        "real_people_behind_characters": info.get("real_people_in_show", {}),
        # Full Wikipedia-sourced structured block
        "wiki": {
            "real_person":         info.get("real_person"),
            "birth_date":          info.get("birth_date"),
            "death_date":          info.get("death_date"),
            "nationality":         info.get("nationality"),
            "crimes":              info.get("crimes", []),
            "network":             info.get("network"),
            "premiere_year":       info.get("premiere_year"),
            "real_people_in_show": info.get("real_people_in_show", {}),
            "sources":             info.get("sources", []),
        },
    }
