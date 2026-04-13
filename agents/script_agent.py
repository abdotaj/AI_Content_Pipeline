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

    # ── PART 1: Script body ───────────────────────────────────────────────────
    part1_prompt = f"""You are a top true crime documentary writer for YouTube.
Write a compelling 1500-word minimum script about: {topic['topic']}

Use this EXACT structure:

HOOK (50 words):
- Most shocking fact to open with
- Make viewer unable to stop listening
- Example: "In 1994, a man walked free after killing 33 people. This is how he did it."

BACKGROUND (300 words):
- Who is the real person/case
- Where and when it happened
- What was life like before the crime

MAIN STORY (700 words):
- Full chronological story with real facts
- Key events in detail
- What the movie/series got right
- What the movie/series changed or exaggerated
- Real quotes from people involved
- Specific dates, names, places

SHOCKING REVELATIONS (300 words):
- 3-5 facts most people don't know
- The darkest details
- What happened behind the scenes

CONCLUSION (150 words):
- What happened after
- Where are they now
- Legacy and impact
- Tease next video with a question
- End with: like, subscribe, comment

TOTAL TARGET: 1500 words minimum, 1800 words maximum.

STRICT WRITING RULES:
1. NEVER start two consecutive sentences with the same word
2. NEVER use "He was" more than once per paragraph
3. Use varied sentence starters — rotate through these styles:
   - Year: "In 1993..."
   - Place: "In Medellín..."
   - Number: "30 billion dollars..."
   - Action subject: "Colombian police..." / "The cartel..."
   - Age: "By age 25..." / "At just 12 years old..."
   - Reveal: "Nobody knew..." / "What the show never revealed..."
   - Contrast: "The truth is..." / "What Netflix changed..."
   - Address viewer: "What you probably don't know..."
4. Each sentence must contain exactly ONE specific fact (name, number, date, or place)
5. Mix sentence lengths — short punchy sentences after long ones
6. Use "..." for dramatic pauses between shocking revelations

BANNED PHRASES — never use these, replace with specific facts:
- "shaped by his experiences" → use the actual experience
- "complex figure" → describe one specific contradiction
- "product of his environment" → name the environment and what happened there
- "rose to infamy" → use "By 1989, he was earning 420 million dollars per week"
- "criminal mastermind" → describe one specific scheme
- "hero to some" → "He built 84 football fields for poor children in Medellín"
- "delve into" / "it is worth noting" / "fascinating" → cut entirely
- NEVER repeat the same fact twice

EXAMPLE OF GOOD WRITING STYLE (copy this rhythm):
"In 1975, a 26-year-old nobody from Medellín made his first cocaine shipment to the United States.
The package was hidden inside a spare tire.
It earned him 100,000 dollars in one week.
Three years later, he controlled 80 percent of the cocaine entering America.
The Medellín Cartel was born.
What Narcos never showed you is what happened next..."

Research data to use:
{research_facts}
{research_inaccuracies}
{research_shocking}

Topic: {topic['topic']}
Series/Movie: {series}

Start the script immediately with the HOOK. Do not add any section labels — write the spoken words only."""

    r1 = _groq_call(
        messages=[{"role": "user", "content": part1_prompt}],
        temperature=0.85,
        max_tokens=4000,
    )
    script_text = r1.choices[0].message.content.strip()

    # ── PART 2: Generate metadata only (title, hook, captions, etc.) ────────
    _series_info    = get_series_for_person(topic["topic"])
    _related_series = f"{_series_info[0]} {_series_info[1]}" if _series_info else series
    part2_prompt = f"""You are a content packaging assistant.
Based on this voiceover script about "{topic['topic']}", generate the metadata fields.

Script summary (first 300 chars): {script_text[:300]}...

TITLE FORMAT (mandatory):
"Dark Crime Decoded: [Real Person] & [Movie/Series Type] — [Shocking Hook]"
Example: "Dark Crime Decoded: Pablo Escobar & Narcos Series — The Truth Netflix Never Showed"
Example: "Dark Crime Decoded: Jordan Belfort & Wolf of Wall Street Movie — The Real Greed"
The real person for this topic is extracted from: {topic['topic']}
The related movie/series with type label is: {_related_series}
If no series is known, use: "Dark Crime Decoded: [Real Person] — [Shocking Hook]"
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
    _fallback_title = (
        f"Dark Crime Decoded: {topic['topic']} & {_related_series} — True Story"
        if _series_info else f"Dark Crime Decoded: {topic['topic']} — True Story"
    )
    script_data = {
        "title":          meta.get("title", _fallback_title),
        "hook":           meta.get("hook", ""),
        "script":         script_text,
        "on_screen_texts": meta.get("on_screen_texts", []),
        "caption":        meta.get("caption", ""),
        "hashtags":       meta.get("hashtags", ""),
        "thumbnail_text": meta.get("thumbnail_text", ""),
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
    }
    ar_data["topic"]        = en_script["topic"]
    ar_data["niche"]        = en_script["niche"]
    ar_data["search_query"] = en_script["search_query"]
    ar_data["keywords"]     = en_script["keywords"]
    ar_data["language"]     = "arabic"
    print(f"[Script] Translated (arabic): '{ar_data['title']}'")
    return ar_data


def write_short_script(en_long_script: dict) -> dict:
    """Generate a ~130-word hook script for a 55-second short video."""
    prompt = f"""You are creating a 55-second short video for TikTok and YouTube Shorts.
Write a punchy 130-word voiceover script based on the topic below.

Topic: {en_long_script['topic']}
Full script opening (for context): {en_long_script['script'][:600]}

REQUIREMENTS:
- Write EXACTLY 130 words — count every word
- Opening: one shocking hook sentence to stop the scroll
- Middle: 2-3 most explosive real facts from the story
- End: "Follow Dark Crime Decoded for the full story"
- No headers, no bullet points — continuous spoken text only

Output ONLY the script text, nothing else."""

    r = _groq_call(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=400,
    )
    script_text = r.choices[0].message.content.strip()

    short_data = {
        "title":           en_long_script.get("title", ""),
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
