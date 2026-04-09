# ============================================================
#  agents/script_agent.py  —  Writes bilingual video scripts
#  English for YouTube, Arabic is a direct translation
# ============================================================
import json
from groq import Groq
from config import GROQ_API_KEY, VIDEO_DURATION_SECONDS

client = Groq(api_key=GROQ_API_KEY)

title_format = "Dark Crime Decoded: {series} — {curiosity_hook}"


def write_script(topic: dict, language: str = "english") -> dict:
    word_count = int(VIDEO_DURATION_SECONDS * 2.3)

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

    prompt = f"""You are an expert faceless content creator specialising in true crime documentaries.
Write a complete viral long-form video package in English for this topic.

Topic: {topic['topic']}
Angle: {topic['angle']}
Niche: {topic['niche']}
Target length: {VIDEO_DURATION_SECONDS} seconds
{research_block}
CRITICAL WORD COUNT REQUIREMENT:
The "script" field MUST contain AT LEAST {word_count} words. This is non-negotiable.
Write in continuous paragraphs — do NOT use bullet points or headers inside the script.
Structure: hook (50w) → background (200w) → rise to power (300w) → key events (400w) → downfall (300w) → aftermath (200w) → shocking facts (150w) → call to action (56w).
Do not stop early. Keep writing until you reach {word_count} words.
Ground every fact in the verified research — cite real names, real dates, real events.

Title format: "Dark Crime Decoded: {{series}} — {{curiosity_hook}}" (max 80 chars)
Style: investigative documentary — build tension, reveal real-world facts, end with a strong call to action.

Return ONLY this JSON with no extra text:
{{
  "title": "Title following the format above in English",
  "hook": "First 3-second spoken hook in English",
  "script": "Full voiceover script in English (~{word_count} words). No emojis. Investigative documentary tone. End with call to action.",
  "on_screen_texts": [
    "Short text at second 0",
    "Short text at second 10",
    "Short text at second 20",
    "Short text at second 35"
  ],
  "caption": "Caption (2-3 sentences) in English",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "4-word thumbnail text in English"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=8000,
        response_format={"type": "json_object"}
    )
    script_data = json.loads(response.choices[0].message.content.strip())
    script_data["topic"] = topic["topic"]
    script_data["niche"] = topic["niche"]
    script_data["search_query"] = topic["search_query"]
    script_data["keywords"] = topic["keywords"]
    script_data["language"] = "english"
    print(f"[Script] Written (english): '{script_data['title']}'")
    return script_data


def translate_script(en_script: dict) -> dict:
    """Translate an English script_data dict into Arabic, keeping all metadata."""
    prompt = f"""Translate the following video script package from English to Arabic.
Translate every text field accurately and naturally — do NOT rewrite or invent new content.
Keep the same structure, tone, and facts. Only translate the text values.

English title: {en_script['title']}
English hook: {en_script['hook']}
English script: {en_script['script']}
English on_screen_texts: {json.dumps(en_script['on_screen_texts'])}
English caption: {en_script['caption']}
English hashtags: {en_script['hashtags']}
English thumbnail_text: {en_script['thumbnail_text']}

Return ONLY this JSON with no extra text:
{{
  "title": "Arabic translation of title",
  "hook": "Arabic translation of hook",
  "script": "Arabic translation of full script",
  "on_screen_texts": ["Arabic text 1", "Arabic text 2", "Arabic text 3", "Arabic text 4"],
  "caption": "Arabic translation of caption",
  "hashtags": "Arabic/transliterated hashtags",
  "thumbnail_text": "Arabic translation of thumbnail text"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=8000,
        response_format={"type": "json_object"}
    )
    ar_data = json.loads(response.choices[0].message.content.strip())
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
