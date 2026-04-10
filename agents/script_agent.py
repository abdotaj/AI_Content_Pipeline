# ============================================================
#  agents/script_agent.py  —  Writes bilingual video scripts
#  English for YouTube, Arabic is a direct translation
# ============================================================
import json
from groq import Groq
from config import GROQ_API_KEY

client = Groq(api_key=GROQ_API_KEY)

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

    r1 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": part1_prompt}],
        temperature=0.85,
        max_tokens=4000,
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

    r2 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
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


def translate_script(en_script: dict) -> dict:
    """Translate an English script_data dict into Arabic, keeping all metadata."""
    # ── Part 1: Translate the script body only ──────────────────────────────
    t1_prompt = f"""Translate the following English voiceover script to Arabic.
Output ONLY the Arabic translation — no labels, no JSON, no preamble.
Keep the same tone, structure, and all facts. Translate naturally and accurately.

English script:
{en_script['script']}"""

    t1 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": t1_prompt}],
        temperature=0.3,
        max_tokens=3000,
    )
    ar_script_text = t1.choices[0].message.content.strip()

    # ── Part 2: Translate short metadata fields only ─────────────────────────
    t2_prompt = f"""Translate these English video metadata fields to Arabic.
Return ONLY this JSON with no extra text:
{{
  "title": "Arabic translation of: {en_script['title']}",
  "hook": "Arabic translation of: {en_script['hook']}",
  "on_screen_texts": ["Arabic of: {en_script['on_screen_texts'][0] if en_script['on_screen_texts'] else ''}", "Arabic of: {en_script['on_screen_texts'][1] if len(en_script['on_screen_texts']) > 1 else ''}", "Arabic of: {en_script['on_screen_texts'][2] if len(en_script['on_screen_texts']) > 2 else ''}", "Arabic of: {en_script['on_screen_texts'][3] if len(en_script['on_screen_texts']) > 3 else ''}"],
  "caption": "Arabic translation of: {en_script['caption']}",
  "hashtags": "Arabic/transliterated hashtags based on: {en_script['hashtags']}",
  "thumbnail_text": "Arabic translation of: {en_script['thumbnail_text']}"
}}"""

    t2 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": t2_prompt}],
        temperature=0.3,
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    meta = json.loads(t2.choices[0].message.content.strip())
    ar_data = {
        "title":          meta.get("title", en_script["title"]),
        "hook":           meta.get("hook", en_script["hook"]),
        "script":         ar_script_text,
        "on_screen_texts": meta.get("on_screen_texts", en_script["on_screen_texts"]),
        "caption":        meta.get("caption", en_script["caption"]),
        "hashtags":       meta.get("hashtags", en_script["hashtags"]),
        "thumbnail_text": meta.get("thumbnail_text", en_script["thumbnail_text"]),
    }
    # Copy non-text metadata from English version
    ar_data["topic"]        = en_script["topic"]
    ar_data["niche"]        = en_script["niche"]
    ar_data["search_query"] = en_script["search_query"]
    ar_data["keywords"]     = en_script["keywords"]
    ar_data["language"]     = "arabic"
    print(f"[Script] Translated (arabic): '{ar_data['title']}'")
    return ar_data


def write_scripts(topics: list[dict]) -> list[dict]:
    """Write English script then translate to Arabic for each topic."""
    scripts = []
    for topic in topics:
        en_script = write_script(topic, language="english")   # YouTube
        ar_script = translate_script(en_script)                # TikTok + X
        scripts.append(ar_script)
        scripts.append(en_script)
    return scripts
