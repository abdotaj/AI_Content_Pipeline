# ============================================================
#  agents/research_agent.py  —  Finds trending topics daily
#  Using Google Gemini (free) instead of OpenAI
# ============================================================
import random
import json
from google import genai
from config import GEMINI_API_KEY, NICHES, NICHE_WEIGHTS

client = genai.Client(api_key=GEMINI_API_KEY)


def pick_niche() -> str:
    """Randomly pick a niche weighted by priority."""
    return random.choices(NICHES, weights=NICHE_WEIGHTS, k=1)[0]


def get_trending_topic(niche: str) -> dict:
    """
    Ask GPT to suggest a trending topic + angle for the given niche.
    Returns a dict with topic, angle, and keywords.
    """
    prompt = f"""
You are a viral social media content strategist for faceless YouTube and TikTok channels.

Today's niche: {niche}

Give me ONE highly trending and specific topic right now in this niche that would get massive views on TikTok and YouTube Shorts.

Respond in this exact JSON format:
{{
  "topic": "The specific trending topic",
  "angle": "The unique angle or hook that makes it viral",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "search_query": "3-word Pexels video search query for background footage"
}}

Only return the JSON. No extra text.
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
    result = json.loads(text)
    result["niche"] = niche
    return result


def research_topics(count: int = 2) -> list[dict]:
    """Generate `count` topic ideas across niches."""
    topics = []
    used_niches = set()

    for _ in range(count):
        # Try to use different niches for variety
        niche = pick_niche()
        attempts = 0
        while niche in used_niches and attempts < 5:
            niche = pick_niche()
            attempts += 1
        used_niches.add(niche)

        topic = get_trending_topic(niche)
        topics.append(topic)
        print(f"[Research] Found topic: {topic['topic']} ({niche})")

    return topics


if __name__ == "__main__":
    topics = research_topics(2)
    for t in topics:
        print(t)
