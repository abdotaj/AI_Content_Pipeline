# ============================================================
#  agents/script_agent.py  —  Writes viral video scripts
#  Using Google Gemini (free) instead of OpenAI
# ============================================================
import json
from google import genai
from config import GEMINI_API_KEY, VIDEO_DURATION_SECONDS

client = genai.Client(api_key=GEMINI_API_KEY)


def write_script(topic: dict) -> dict:
    """
    Takes a topic dict from research_agent and returns a full video package:
    hook, script, captions, hashtags, and title.
    """
    word_count = int(VIDEO_DURATION_SECONDS * 2.3)  # ~2.3 words/sec speech rate

    prompt = f"""
You are an expert faceless content creator for TikTok and YouTube Shorts.
Write a complete viral short video package for this topic.

Topic: {topic['topic']}
Angle: {topic['angle']}
Niche: {topic['niche']}
Target length: {VIDEO_DURATION_SECONDS} seconds (~{word_count} words spoken)

Return ONLY this JSON structure:

{{
  "title": "YouTube/TikTok video title (max 60 chars, no clickbait)",
  "hook": "First 3-second spoken hook sentence — must grab attention instantly",
  "script": "Full voiceover script ({word_count} words). No emojis. Conversational. End with a call to action to follow.",
  "on_screen_texts": [
    "Short text shown on screen at second 0",
    "Short text shown at second 10",
    "Short text shown at second 20",
    "Short text shown at second 35"
  ],
  "caption": "TikTok/YouTube caption (2-3 sentences, engaging, includes call to action)",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "Bold 4-word thumbnail text"
}}

Only return valid JSON. No markdown.
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    script_data = json.loads(text)
    script_data["topic"] = topic["topic"]
    script_data["niche"] = topic["niche"]
    script_data["search_query"] = topic["search_query"]
    script_data["keywords"] = topic["keywords"]

    print(f"[Script] Written: '{script_data['title']}'")
    return script_data


def write_scripts(topics: list[dict]) -> list[dict]:
    """Write scripts for a list of topics."""
    return [write_script(t) for t in topics]


if __name__ == "__main__":
    sample_topic = {
        "topic": "GPT-5 just changed everything",
        "angle": "What it means for jobs in 2025",
        "niche": "AI & Tech news",
        "keywords": ["GPT-5", "AI jobs", "future"],
        "search_query": "artificial intelligence technology"
    }
    script = write_script(sample_topic)
    print(json.dumps(script, indent=2))
