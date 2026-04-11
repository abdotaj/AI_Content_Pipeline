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


title_format = "Dark Crime Decoded: {series} — {curiosity_hook}"


def write_script(topic: dict, language: str = "english") -> dict:
    word_count = 900  # ~6-7 min voiceover; safe for Groq token limits

    # Inject web-researched facts if available
    research = topic.get("research", {})
    research_block = ""
    if research and research.get("real_story"):
        facts_right  = "\n".join(f"  - {f}" for f in research.get("what_show_got_right", []))
        facts_wrong  = "\n".join(f"  - {f}" for f in research.get("what_show_got_wrong", []))
        shocking     = "\n".join(f"  - {f}" for f in research.get("shocking_real_facts", []))
        real_people  = "\n".join(
            f"  - {k}: {v}" for k, v in research.get("real_people_behind_characters", {}).items()
        )
        research_block = f"""
VERIFIED RESEARCH FACTS (use these — do not invent):
Real story: {research['real_story']}
What the show got right:
{facts_right}
What the show got wrong / dramatized:
{facts_wrong}
Shocking real facts to include:
{shocking}
Real people behind characters:
{real_people}
"""

    # ── PART 1: Generate plain-text script (bulk content) ───────────────────
    part1_prompt = f"""You are an expert faceless content creator specialising in true crime documentaries.
Write a LONG investigative voiceover script in English for this topic.

Topic: {topic['topic']}
Angle: {topic['angle']}
Niche: {topic['niche']}
{research_block}
REQUIREMENTS:
- Write EXACTLY {word_count} words or more — count every word before finishing
- Continuous paragraphs only — NO bullet points, NO headers, NO emojis
- Structure: opening hook (60w) → historical background (150w) → rise to power (200w) → key criminal events (200w) → downfall (150w) → aftermath & legacy (100w) → shocking real facts (80w) → call to action (60w)
- Investigative documentary tone — build tension, cite real names, real dates, real events
- End with a strong call to action asking viewers to like, subscribe and comment

Start the script immediately without preamble. Write every section fully. Do not stop until you have written {word_count} words."""

    r1 = _groq_call(
        messages=[{"role": "user", "content": part1_prompt}],
        temperature=0.85,
        max_tokens=2000,
    )
    script_text = r1.choices[0].message.content.strip()

    # ── PART 2: Generate metadata only (title, hook, captions, etc.) ────────
    # Script body comes from Part 1; Part 2 only produces short fields
    part2_prompt = f"""You are a content packaging assistant.
Based on this voiceover script about "{topic['topic']}", generate the metadata fields.

Script summary (first 300 chars): {script_text[:300]}...

Return ONLY this JSON with no extra text:
{{
  "title": "Dark Crime Decoded: [series name] — [curiosity hook]  (max 80 chars)",
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
    script_data = {
        "title":          meta.get("title", f"Dark Crime Decoded: {topic['topic']}"),
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
        "title":          translate_to_arabic(en_script["title"]),
        "hook":           translate_to_arabic(en_script["hook"]),
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
        "title":           en_long_script["title"],
        "hook":            en_long_script["hook"],
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
