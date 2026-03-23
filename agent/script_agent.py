import json
from groq import Groq
from config import GROQ_API_KEY, VIDEO_DURATION_SECONDS

client = Groq(api_key=GROQ_API_KEY)


def write_script(topic: dict) -> dict:
    word_count = int(VIDEO_DURATION_SECONDS * 2.3)

    prompt = f"""You are an expert faceless content creator for TikTok and YouTube Shorts.
Write a complete viral short video package for this topic.

Topic: {topic['topic']}
Angle: {topic['angle']}
Niche: {topic['niche']}
Target length: {VIDEO_DURATION_SECONDS} seconds (~{word_count} words spoken)

Return ONLY this JSON with no extra text:
{{
  "title": "YouTube/TikTok video title (max 60 chars)",
  "hook": "First 3-second spoken hook sentence",
  "script": "Full voiceover script ({word_count} words). No emojis. Conversational. End with call to action.",
  "on_screen_texts": [
    "Short text at second 0",
    "Short text at second 10",
    "Short text at second 20",
    "Short text at second 35"
  ],
  "caption": "TikTok/YouTube caption (2-3 sentences)",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "Bold 4-word thumbnail text"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        response_format={"type": "json_object"}
    )
    script_data = json.loads(response.choices[0].message.content.strip())
    script_data["topic"] = topic["topic"]
    script_data["niche"] = topic["niche"]
    script_data["search_query"] = topic["search_query"]
    script_data["keywords"] = topic["keywords"]
    print(f"[Script] Written: '{script_data['title']}'")
    return script_data


def write_scripts(topics: list[dict]) -> list[dict]:
    return [write_script(t) for t in topics]
