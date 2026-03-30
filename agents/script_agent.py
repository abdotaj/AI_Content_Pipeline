# ============================================================
#  agents/script_agent.py  —  Writes bilingual video scripts
#  Arabic for TikTok/X, English for YouTube
# ============================================================
import json
from groq import Groq
from config import GROQ_API_KEY, VIDEO_DURATION_SECONDS

client = Groq(api_key=GROQ_API_KEY)


def write_script(topic: dict, language: str = "english") -> dict:
    word_count = int(VIDEO_DURATION_SECONDS * 2.3)
    lang_instruction = "in Arabic" if language == "arabic" else "in English"

    is_deepfake_niche = topic.get("niche") == "AI Deepfakes & Real vs Fake"
    deepfake_instruction = ""
    if is_deepfake_niche:
        if language == "arabic":
            deepfake_instruction = """
Style: "compare and expose" — structure the script to contrast real vs fake examples, build suspense, then reveal.
Use hooks like "هل تستطيع تمييز الفيديو المزيف؟" or "هذا الفيديو مزيف بالذكاء الاصطناعي!" or "90% من الناس لا يستطيعون كشف هذا!".
Guide the viewer step-by-step on how to detect fakes. End with a warning call to action."""
        else:
            deepfake_instruction = """
Style: "compare and expose" — structure the script to contrast real vs fake examples, build suspense, then reveal.
Use hooks like "Can you tell which one is AI?" or "This video is 100% AI-generated!" or "90% of people can't spot this fake!".
Guide the viewer step-by-step on how to detect fakes. End with a warning call to action."""

    prompt = f"""You are an expert faceless content creator for TikTok and YouTube Shorts.
Write a complete viral short video package {lang_instruction} for this topic.

Topic: {topic['topic']}
Angle: {topic['angle']}
Niche: {topic['niche']}
Target length: {VIDEO_DURATION_SECONDS} seconds (~{word_count} words spoken)

Allowed niches: AI & Tech news, Space & Astronomy facts, Motivation & mindset, History & civilization, Science & discoveries, AI Deepfakes & Real vs Fake.
Do NOT include content about animals, pets, wildlife, or nature.{deepfake_instruction}

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
