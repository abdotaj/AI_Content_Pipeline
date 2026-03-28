import random
import json
from groq import Groq
from config import GROQ_API_KEY, NICHES, NICHE_WEIGHTS

client = Groq(api_key=GROQ_API_KEY)


def pick_niche() -> str:
    return random.choices(NICHES, weights=NICHE_WEIGHTS, k=1)[0]


def get_trending_topic(niche: str) -> dict:
    prompt = f"""You are a viral social media content strategist for faceless YouTube and TikTok channels.

Today's niche: {niche}

Give me ONE highly trending and specific topic in this niche for TikTok and YouTube Shorts.

Respond in this exact JSON format with no extra text:
{{
  "topic": "The specific trending topic",
  "angle": "The unique angle that makes it viral",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "search_query": "3-word Pexels video search query"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        response_format={"type": "json_object"}
    )
    result = json.loads(response.choices[0].message.content.strip())
    result["niche"] = niche
    return result


def research_topics(count: int = 2) -> list[dict]:
    topics = []
    used_niches = set()
    for _ in range(count):
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
