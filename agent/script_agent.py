# ============================================================
#  agents/script_agent.py  —  Writes bilingual video scripts
#  English for YouTube, Arabic is a direct translation
# ============================================================
import json
import groq as groq_lib
from groq import Groq
from config import GROQ_API_KEY

_groq = Groq(api_key=GROQ_API_KEY)

_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",   # primary
    "llama-3.1-8b-instant",      # fallback
]


def _groq_call(**kwargs):
    """Try each model with one 40-second retry on rate limit before moving to fallback."""
    import time
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


title_format = "Dark Crime Decoded: {person} & {series} — {curiosity_hook}"

PERSON_TO_SERIES: dict[str, tuple[str, str]] = {
    "pablo escobar":   ("Narcos",                "Series"),
    "escobar":         ("Narcos",                "Series"),
    "al capone":       ("Boardwalk Empire",       "Series"),
    "capone":          ("Boardwalk Empire",       "Series"),
    "jeffrey dahmer":  ("Monster",               "Series"),
    "dahmer":          ("Monster",               "Series"),
    "el chapo":        ("Narcos Mexico",          "Series"),
    "griselda blanco": ("Griselda",              "Series"),
    "jordan belfort":  ("Wolf of Wall Street",   "Movie"),
    "john gotti":      ("Gotti",                 "Movie"),
    "btk":             ("Mindhunter",            "Series"),
    "ted bundy":       ("Extremely Wicked",      "Movie"),
    "ed gein":         ("Psycho",                "Movie"),
    "lucky luciano":   ("The Godfather",         "Movie"),
    "frank lucas":     ("American Gangster",     "Movie"),
    "henry hill":      ("Goodfellas",            "Movie"),
    "whitey bulger":   ("Black Mass",            "Movie"),
    "dexter morgan":   ("Dexter",                "Series"),
    "dexter":          ("Dexter",                "Series"),
    "btk killer":      ("BTK",                   "Series"),
    "night stalker":   ("Night Stalker",         "Series"),
    "richard ramirez": ("Night Stalker",         "Series"),
    "charles manson":  ("Mindhunter",            "Series"),
    "amanda knox":     ("Stillwater",            "Movie"),
    "leopold":         ("Rope",                  "Movie"),
    "loeb":            ("Rope",                  "Movie"),
    "kitty genovese":  ("Kitty",                 "Movie"),
    "wm3":             ("Devil's Knot",          "Movie"),
    "west memphis":    ("Devil's Knot",          "Movie"),
}


def get_series_for_person(topic_text: str) -> tuple[str, str] | None:
    """Return (series_name, type) tuple or None if no match."""
    topic_lower = topic_text.lower()
    for person, info in PERSON_TO_SERIES.items():
        if person in topic_lower:
            return info
    return None


_DARKCRIMED_BASE_HASHTAGS = [
    "#DarkCrimeDecoded", "#TrueCrime", "#RealStory", "#CrimeDocumentary",
]
_DARKCRIMED_BASE_AR_HASHTAGS = [
    "#جريمة_حقيقية", "#وثائقي_جريمة", "#دارك_كرايم_ديكودد",
]


def generate_chapters(script, total_duration_seconds=1200):
    """Return YouTube chapter timestamps for a 20-minute documentary."""
    chapters = [
        (0,    "🎬 Introduction"),
        (60,   "📺 What The Series Showed"),
        (180,  "🔍 The Real Story"),
        (420,  "😱 What Netflix Changed"),
        (660,  "💀 Shocking Facts They Left Out"),
        (900,  "⚖️ Series vs Reality"),
        (1100, "🎯 The Truth & Conclusion"),
    ]
    chapter_text = ""
    for seconds, title in chapters:
        mins = seconds // 60
        secs = seconds % 60
        chapter_text += f"{mins:02d}:{secs:02d} {title}\n"
    return chapter_text


def add_short_title(script_data: dict) -> str:
    """Generate a clickable short video title with emoji via Groq."""
    topic = script_data.get("topic", "")
    _si   = get_series_for_person(topic)
    series = _si[0] if _si else script_data.get("niche", "")
    series_tag = f"#{series.replace(' ', '')}" if series else ""

    prompt = f"""Generate ONE punchy YouTube Shorts / TikTok title for a true crime short video.

Topic: {topic}
Related series/movie: {series}

RULES:
- Max 60 characters total
- CAPITALISE one shocking word: LIED, REAL, NEVER, HIDDEN, WORSE, DARKER, CHANGED
- End with ONE relevant emoji chosen from: 🔴 😱 🔍 💀 🎬
- Add the series hashtag ({series_tag}) if a series is known
- NO "Dark Crime Decoded:" prefix — this is for Shorts/TikTok

EXAMPLES:
"Netflix LIED about Pablo Escobar #Narcos 🔴"
"The REAL Dexter Morgan was 10x worse #Dexter 😱"
"What Narcos NEVER showed you 🔍"
"Al Capone's secret Netflix hid 💀"

Output ONLY the title text, nothing else."""

    r = _groq_call(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=80,
    )
    return r.choices[0].message.content.strip().strip('"\'')


def _build_darkcrimed_hashtags(raw: str, series_info: tuple[str, str] | None) -> str:
    """
    Prepend series/movie tags and guarantee base tags are present.
    raw: space-separated hashtag string from Groq (may include Arabic tags).
    """
    tags = raw.split() if raw else []

    prefix: list[str] = []
    if series_info:
        series_name, series_type = series_info
        series_tag = "#" + series_name.replace(" ", "")   # e.g. #Narcos
        type_tag   = "#" + series_type                     # e.g. #Series
        if series_tag not in tags:
            prefix.append(series_tag)
        if type_tag not in tags:
            prefix.append(type_tag)

    for tag in _DARKCRIMED_BASE_HASHTAGS + _DARKCRIMED_BASE_AR_HASHTAGS:
        if tag not in tags:
            tags.append(tag)

    return " ".join(prefix + tags)


def _is_shopmart() -> bool:
    """Return True when the pipeline is running for Shopmart Global."""
    try:
        import config as _cfg
        return "shopmart" in getattr(_cfg, "CHANNEL", "").lower()
    except Exception:
        return False


def write_script(topic: dict, language: str = "english") -> dict:
    if _is_shopmart():
        return _write_shopmart_script(topic)
    return _write_darkcrimed_script(topic)


def _write_shopmart_script(topic: dict) -> dict:
    """Product review / top-list style script for Shopmart Global."""
    word_count = 130  # ~55-second short video

    part1_prompt = f"""You are a product review content creator for YouTube Shorts and TikTok.
Write a punchy {word_count}-word voiceover script for the topic below.

Topic: {topic['topic']}
Niche: {topic['niche']}

REQUIREMENTS:
- Write EXACTLY {word_count} words — count every word before finishing
- Opening: one attention-grabbing hook that stops the scroll (1-2 sentences)
- Middle: 3-5 short punchy product benefits or reasons to buy — one per line
- Closing: strong call to action ("Link in bio", "Buy now before it sells out", "Check the link below")
- NO documentary tone, NO crime references, NO headers, NO bullet points
- Write like an enthusiastic product reviewer speaking to camera
- Short sentences, maximum 12 words each
- Use '...' for natural spoken pauses

Output ONLY the script text, nothing else."""

    r1 = _groq_call(
        messages=[{"role": "user", "content": part1_prompt}],
        temperature=0.85,
        max_tokens=400,
    )
    script_text = r1.choices[0].message.content.strip()

    part2_prompt = f"""You are a content packaging assistant for an ecommerce channel called Shopmart.
Based on this product review script, generate metadata.

Topic: {topic['topic']}
Script (first 200 chars): {script_text[:200]}...

Return ONLY this JSON with no extra text:
{{
  "title": "Shopmart: [product/topic] — [short hook] (max 80 chars)",
  "hook": "First spoken hook sentence (max 15 words)",
  "on_screen_texts": [
    "Bold text for second 0",
    "Bold text for second 10",
    "Bold text for second 25",
    "Bold text for second 45"
  ],
  "caption": "2-3 sentence caption with product benefits and a buy link CTA",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "4-word thumbnail text"
}}"""

    r2 = _groq_call(
        messages=[{"role": "user", "content": part2_prompt}],
        temperature=0.3,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    meta = json.loads(r2.choices[0].message.content.strip())
    script_data = {
        "title":           meta.get("title", f"Shopmart: {topic['topic']}"),
        "hook":            meta.get("hook", ""),
        "script":          script_text,
        "on_screen_texts": meta.get("on_screen_texts", []),
        "caption":         meta.get("caption", ""),
        "hashtags":        meta.get("hashtags", ""),
        "thumbnail_text":  meta.get("thumbnail_text", ""),
        "topic":           topic["topic"],
        "niche":           topic["niche"],
        "search_query":    topic.get("search_query", ""),
        "keywords":        topic.get("keywords", []),
        "language":        "english",
    }
    print(f"[Script] Written (shopmart english): '{script_data['title']}'")
    return script_data


def _write_darkcrimed_script(topic: dict) -> dict:
    """Investigative documentary script for Dark Crime Decoded."""
    research = topic.get("research", {})
    series   = topic.get("series", topic.get("niche", ""))

    # Use new structured fields if available, fall back to legacy fields
    facts_list       = research.get("research_facts")        or research.get("what_show_got_right", [])
    inaccuracy_list  = research.get("research_inaccuracies") or research.get("what_show_got_wrong", [])
    shocking_list    = research.get("research_shocking")     or research.get("shocking_real_facts", [])

    research_facts        = "\n".join(f"- {f}" for f in facts_list)       or "(research the real story)"
    research_inaccuracies = "\n".join(f"- {i}" for i in inaccuracy_list)  or "(research what the show dramatized)"
    research_shocking     = "\n".join(f"- {s}" for s in shocking_list)    or "(include surprising real details)"

    # Wikipedia-sourced verified data (may be None if DDG fallback was used)
    wiki_network      = research.get("network") or "the network"
    wiki_year         = research.get("premiere_year") or "unknown year"
    wiki_real_person  = research.get("real_person") or topic.get("topic", "")

    # ── PART 1: Script body ───────────────────────────────────────────────────
    _si_long     = get_series_for_person(topic["topic"])
    series_label = f"{_si_long[0]} {_si_long[1]}" if _si_long else series

    part1_prompt = f"""You are a top true crime documentary writer for YouTube.
Write a 3000-3500 word 20-minute documentary script about: {topic['topic']}
The related series/movie is: {series_label}

CRITICAL: Use ONLY these verified Wikipedia facts. Do NOT invent any information.
Network: {wiki_network}
Series premiered: {wiki_year}
Real person: {wiki_real_person}

VERIFIED FACTS (from Wikipedia):
{research_facts}

WHAT THE SHOW CHANGED (from Wikipedia):
{research_inaccuracies}

SHOCKING FACTS (from Wikipedia):
{research_shocking}

If you are not 100% sure about a fact — do not include it.
Always say "{wiki_network}" not "Netflix" unless the network IS Netflix.

Use this EXACT structure (no section labels in the output — spoken words only):

HOOK (80 words):
- Most shocking single fact about this case
- Something that stops the viewer immediately
- Example: "Netflix spent 50 million dollars making {series_label}. But they changed one key detail that changes everything."

SERIES INTRO (250 words):
- What {series_label} showed the world
- Why millions of people watched it
- Set up the question: but what really happened?
- Name {series_label} directly and what it got famous for

REAL BACKGROUND (600 words):
- Real person's early life with specific facts
- Family, childhood, first crimes — real dates, real places, real names
- What shaped them BEFORE the series begins

MAIN STORY (1200 words):
- Full chronological real story
- Key events the series covered — what {series_label} got RIGHT with evidence
- What {series_label} CHANGED and why Hollywood altered it
- Real quotes from people involved
- Specific dates and facts throughout

SHOCKING REVELATIONS (500 words):
- 4-5 facts {series_label} completely left out
- The darkest real details
- Things that would shock even fans of the show
- Real impact on real people

SERIES VS REALITY (400 words):
- Direct comparisons: "In {series_label}, they showed X. In reality, Y happened."
- 3 specific scene or character comparisons
- What Hollywood changed purely for drama

CONCLUSION (200 words):
- What happened after the events {series_label} depicted
- Where the real people are now
- One question to tease the next video
- End with: "Follow Dark Crime Decoded for more real stories behind your favourite crime series"

TOTAL TARGET: 3000 words minimum, 3500 words maximum.

STRICT WRITING RULES:
1. NEVER start two consecutive sentences with the same word
2. NEVER use "He was" more than once per paragraph
3. Use varied sentence starters: year ("In 1993..."), place, number, action subject, age, reveal, contrast, viewer address
4. Each sentence must contain exactly ONE specific fact (name, number, date, or place)
5. Mix sentence lengths — short punchy sentences after long ones
6. Name {series_label} at least 8 times throughout the script
7. Include at least 10 real dates or numbers
8. Use "..." for dramatic pauses

BANNED PHRASES — replace with specific facts:
- "delve into" / "complex figure" / "shaped by" / "rose to infamy" / "criminal mastermind"
- "hero to some" → use the actual act (e.g. "He built 84 football fields for the poor")
- NEVER repeat the same fact twice

Topic: {topic['topic']}
Series/Movie: {series_label}

Start immediately with the HOOK. Write spoken words only — no labels, no headers."""

    r1 = _groq_call(
        messages=[{"role": "user", "content": part1_prompt}],
        temperature=0.85,
        max_tokens=6000,
    )
    script_text = r1.choices[0].message.content.strip()

    # ── PART 2: Generate metadata only (title, hook, captions, etc.) ────────
    _series_info    = get_series_for_person(topic["topic"])
    _related_series = f"{_series_info[0]} {_series_info[1]}" if _series_info else series
    part2_prompt = f"""You are a content packaging assistant.
Based on this voiceover script about "{topic['topic']}", generate the metadata fields.

TITLE FORMAT (mandatory):
"The REAL [Real Person]: What [Series] Got Wrong | Dark Crime Decoded"
Example: "The REAL Pablo Escobar: What Narcos Got Wrong | Dark Crime Decoded"
Example: "The REAL Jordan Belfort: What Wolf of Wall Street Got Wrong | Dark Crime Decoded"
Example: "The REAL Al Capone: What Boardwalk Empire Got Wrong | Dark Crime Decoded"
The real person for this topic is extracted from: {topic['topic']}
The related series/movie is: {_related_series}
If no series is known, use: "The REAL [Real Person]: The True Story | Dark Crime Decoded"
Max 90 chars total.

Return ONLY this JSON with no extra text:
{{
  "title": "Dark Crime Decoded: [Real Person] & [Movie/Series Type] — [hook]",
  "hook": "First 3-second spoken hook sentence",
  "on_screen_texts": [
    "Short bold text for second 0",
    "Short bold text for second 10",
    "Short bold text for second 20",
    "Short bold text for second 35"
  ],
  "caption": "2-3 sentence caption for social media",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "4-word thumbnail text"
}}"""

    r2 = _groq_call(
        messages=[{"role": "user", "content": part2_prompt}],
        temperature=0.3,
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    meta = json.loads(r2.choices[0].message.content.strip())
    _series_name = _series_info[0] if _series_info else _related_series
    _fallback_title = (
        f"The REAL {topic['topic']}: What {_series_name} Got Wrong | Dark Crime Decoded"
        if _series_info else f"The REAL {topic['topic']}: The True Story | Dark Crime Decoded"
    )
    script_data = {
        "title":          meta.get("title", _fallback_title),
        "hook":           meta.get("hook", ""),
        "script":         script_text,
        "on_screen_texts": meta.get("on_screen_texts", []),
        "caption":        meta.get("caption", ""),
        "hashtags":       _build_darkcrimed_hashtags(meta.get("hashtags", ""), _series_info),
        "thumbnail_text": meta.get("thumbnail_text", ""),
        "chapters":       generate_chapters(script_text),
    }
    script_data["topic"] = topic["topic"]
    script_data["niche"] = topic["niche"]
    script_data["search_query"] = topic["search_query"]
    script_data["keywords"] = topic["keywords"]
    script_data["language"] = "english"
    print(f"[Script] Written (english): '{script_data['title']}'")
    return script_data


def translate_to_arabic(text: str) -> str:
    """Translate English text to Arabic using Google Translate free REST API."""
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "en",
        "tl": "ar",
        "dt": "t",
        "q": text,
    }
    import requests as _requests
    response = _requests.get(url, params=params)
    response.raise_for_status()
    result = response.json()
    translated = "".join([item[0] for item in result[0]])
    return translated


def translate_script(en_script: dict) -> dict:
    """Translate an English script_data dict into Arabic using Google Translate."""
    ar_data = {
        "title":          translate_to_arabic(en_script.get("title", "")),
        "hook":           translate_to_arabic(en_script.get("hook", "")),
        "script":         translate_to_arabic(en_script["script"]),
        "on_screen_texts": [translate_to_arabic(t) for t in en_script["on_screen_texts"]],
        "caption":        translate_to_arabic(en_script["caption"]),
        "hashtags":       translate_to_arabic(en_script["hashtags"]),
        "thumbnail_text": translate_to_arabic(en_script["thumbnail_text"]),
        "chapters":       en_script.get("chapters", ""),  # keep English timestamps
    }
    ar_data["topic"]        = en_script["topic"]
    ar_data["niche"]        = en_script["niche"]
    ar_data["search_query"] = en_script["search_query"]
    ar_data["keywords"]     = en_script["keywords"]
    ar_data["language"]     = "arabic"
    print(f"[Script] Translated (arabic): '{ar_data['title']}'")
    return ar_data


def write_short_script(en_long_script: dict) -> dict:
    """Generate a ~130-word two-part script for a 55-second short video."""
    topic  = en_long_script.get("topic", "")
    _si    = get_series_for_person(topic)
    series = f"{_si[0]} {_si[1]}" if _si else en_long_script.get("niche", "the series")

    prompt = f"""Write a 55-second true crime short script about: {topic}
Related series/movie: {series}

STRUCTURE (follow exactly):

PART 1 — THE REAL PERSON (35 seconds, 80-90 words):
- Start with the most shocking real fact about this person
- Include 3-4 specific facts with real numbers, dates, or places
- Short punchy sentences — maximum 12 words each
- No vague phrases like "rose to infamy" or "criminal mastermind"

PART 2 — THE SERIES CONNECTION (20 seconds, 45-55 words):
- Name the series/movie directly: "{series}"
- State ONE key thing the show got wrong or left out
- Compare fiction vs reality with one specific fact
- End with exactly: "Follow Dark Crime Decoded for the full story"

RULES:
- Total 130-145 words only — count before finishing
- Every sentence must contain ONE specific fact (name, number, date, or place)
- Never start two consecutive sentences with the same word
- Write naturally like speaking to a friend — no headers, no bullet points

Use this context from the full script:
{en_long_script.get('script', '')[:500]}

Output ONLY the spoken script text, nothing else."""

    r = _groq_call(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=450,
    )
    script_text = r.choices[0].message.content.strip()

    short_data = {
        "title":           en_long_script.get("title", ""),  # overwritten below
        "hook":            en_long_script.get("hook", script_text[:100]),
        "script":          script_text,
        "on_screen_texts": en_long_script.get("on_screen_texts", [])[:2],
        "caption":         en_long_script["caption"],
        "hashtags":        en_long_script["hashtags"],
        "thumbnail_text":  en_long_script["thumbnail_text"],
        "topic":           en_long_script["topic"],
        "niche":           en_long_script["niche"],
        "search_query":    en_long_script["search_query"],
        "keywords":        en_long_script["keywords"],
        "language":        "english",
    }
    _short_title = add_short_title(short_data)
    short_data["title"]       = _short_title
    short_data["short_title"] = _short_title
    print(f"[Script] Written (english short): '{short_data['title']}'")
    return short_data


def write_scripts(topics: list[dict]) -> list[dict]:
    """Write English script then translate to Arabic for each topic."""
    scripts = []
    for topic in topics:
        en_script = write_script(topic, language="english")   # YouTube
        ar_script = translate_script(en_script)                # TikTok + X
        scripts.append(ar_script)
        scripts.append(en_script)
    return scripts
