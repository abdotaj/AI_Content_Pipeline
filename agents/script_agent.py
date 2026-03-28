# ============================================================
#  agents/script_agent.py  —  Writes bilingual video scripts
#  Arabic for TikTok/X, English for YouTube
# ============================================================
import json
import anthropic
from config import ANTHROPIC_API_KEY, VIDEO_DURATION_SECONDS

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def write_script(topic: dict, language: str = "english") -> dict:
    word_count = int(VIDEO_DURATION_SECONDS * 2.3)
    lang_instruction = "in Arabic" if language == "arabic" else "in English"

    prompt = f"""You are an expert faceless content creator for TikTok and YouTube Shorts.
Write a complete viral short video package {lang_instruction} for this topic.

Topic: {topic['topic']}
Angle: {topic['angle']}
Niche: {topic['niche']}
Target length: {VIDEO_DURATION_SECONDS} seconds (~{word_count} words spoken)

Return ONLY this JSON with no extra text:
{{
  "title": "Video title (max 60 chars) {lang_instruction}",
  "hook": "First 3-second spoken hook {lang_instruction}",
  "script": "Full voiceover script {lang_instruction} (~{word_count} words). No emojis. End with call to action to follow.",
  "on_screen_texts": [
    "Short text at second 0 {lang_instruction}",
    "Short text at second 10 {lang_instruction}",
    "Short text at second 20 {lang_instruction}",
    "Short text at second 35 {lang_instruction}"
  ],
  "caption": "Caption (2-3 sentences) {lang_instruction}",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "4-word thumbnail text {lang_instruction}"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
    )
    script_data = json.loads(response.content[0].text.strip())
    script_data["topic"] = topic["topic"]
    script_data["niche"] = topic["niche"]
    script_data["search_query"] = topic["search_query"]
    script_data["keywords"] = topic["keywords"]
    script_data["language"] = language
    print(f"[Script] Written ({language}): '{script_data['title']}'")
    return script_data


def write_scripts(topics: list[dict]) -> list[dict]:
    """Write each topic in both Arabic (TikTok/X) and English (YouTube)."""
    scripts = []
    for topic in topics:
        scripts.append(write_script(topic, language="arabic"))   # TikTok + X
        scripts.append(write_script(topic, language="english"))  # YouTube
    return scripts
