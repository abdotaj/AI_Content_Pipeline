# CLAUDE.md — Dark Crime Decoded Pipeline

## Project Overview
AI-powered content pipeline for two YouTube channels:
- Dark Crime Decoded (@DarkCrimeDecoded) — True crime series/movies
- Shopmart Global (@ShopmartGlobal) — Ecommerce content

GitHub: github.com/abdotaj/AI_Content_Pipeline
Local: C:\Users\abdot\content_pipeline

## Entry Points
- run_darkcrimed.py — Dark Crime Decoded pipeline
- run_shopmart.py — Shopmart Global pipeline
- main.py — Legacy entry point

## Daily Output (Dark Crime Decoded)
4 outputs from 1 topic per day:
1. English long-form (10-12 min) → auto YouTube upload
2. Arabic long-form (10-12 min) → auto YouTube upload  
3. English short (55 sec) → Telegram for manual TikTok/Instagram
4. Arabic short (55 sec) → Telegram for manual TikTok/Instagram

## Pipeline Flow
1. Research 1 topic via DuckDuckGo
2. Write English script via Groq (llama-3.3-70b-versatile)
3. Translate to Arabic via Google Translate free API
4. Send 4 scripts to Telegram for review (non-blocking)
5. Generate voiceover via OpenAI TTS → edge-tts fallback
6. Generate 10 AI images via Pollinations API
7. Assemble 4 videos via MoviePy
8. Auto-post 2 long videos to YouTube
9. Send 2 short clips to Telegram

## Voice Configuration
Primary: OpenAI TTS (tts-1-hd)
- English: alloy voice, speed 1.0
- Arabic: nova voice, speed 1.1
Fallback: edge-tts (free, no API key required)
- English: en-US-AriaNeural
- Arabic: ar-SA-ZariyahNeural
Key: OPENAI_API_KEY — in .env and GitHub Secrets
Chunking: long scripts split by [SECTION:] markers, concatenated via ffmpeg

## Image Generation
Provider: Pollinations API (free, no API key)
URL: https://image.pollinations.ai/prompt/{encoded_prompt}
Size: 1080x1920 (vertical 9:16)
Count: 10 images per long video, 6 per short
Style: Portrait → Location → Era → Crime/Theme → Justice
Each image: zoom in clip + zoom out clip = 2 clips
Clips shuffled randomly, looped to fill audio duration

## AI/LLM Stack
- Groq: script writing only (llama-3.3-70b-versatile → llama-3.1-8b-instant fallback)
- Google Translate: Arabic translation (free REST API, no key)
- DuckDuckGo: web research (duckduckgo-search library)
- OpenAI TTS (tts-1-hd): voiceover — primary engine
- edge-tts: voiceover fallback (free, no key)
- Pollinations: AI image generation (free, no key)
- Pexels API: stock photos + videos (PEXELS_API_KEY)
- Pixabay API: stock photos + videos (PIXABAY_API_KEY)

## YouTube Channels
Dark Crime Decoded:
- Token: youtube_token_darkcrimed.json (local only, never commit)
- GitHub Secret: YOUTUBE_TOKEN_JSON_DARKCRIMED
- Client ID Secret: YOUTUBE_CLIENT_ID_DARKCRIMED

Shopmart Global:
- Token: youtube_token_shopmart.json (local only, never commit)
- GitHub Secret: YOUTUBE_TOKEN_JSON_SHOPMART
- Client ID Secret: YOUTUBE_CLIENT_ID_SHOPMART

## GitHub Actions (daily.yml)
- dark-crime-pipeline: 4 AM UTC (7 AM Riyadh) — timeout 360 min
- shopmart-pipeline: runs after dark-crime — timeout 20 min
- Manual trigger: Run workflow → type "darkcrimed" or "shopmart" or "both"

## Key Config Files
- config_darkcrimed.py — Dark Crime settings, niches, paths
- config_shopmart.py — Shopmart settings
- agents/research_agent.py — DuckDuckGo research + topic memory
- agents/script_agent.py — Groq script + Google Translate
- agents/video_agent.py — OpenAI TTS + edge-tts + Pollinations + Pexels + Pixabay + MoviePy
- agents/notify_agent.py — Telegram bot
- agents/publish_agent.py — YouTube upload
- output/covered_topics.json — never repeat a topic

## Content Niches (Dark Crime Decoded)
Real stories behind crime movies and series:
Godfather, Scarface, Narcos, Money Heist, Breaking Bad,
Peaky Blinders, Goodfellas, Casino, Ozark, The Wire,
Griselda, American Gangster, Donnie Brasco, City of God, Sicario

## Known Issues & Fixes
- Windows temp file lock: use unique temp_audiofile path
- Groq rate limit: 6000 TPM free tier, sleep 8s between calls
- OpenAI TTS quota: tracked via _OPENAI_QUOTA_EXCEEDED flag, falls back to edge-tts
- Pollinations 429: retry 3x with 30s wait, PIL fallback
- .claude/ folder: always in .gitignore, never commit
- youtube_token*.json: always in .gitignore, never commit
- content/pending/: always in .gitignore, never commit

## Telegram Bot
Bot: @AAmycontentbot_bot
Chat ID: 737063834
Functions: script preview, short clip delivery, daily report

## Important Rules
- NEVER commit: .env, youtube_token*.json, .claude/, output/
- ALWAYS use .get() for script_data fields in publish_agent
- ALWAYS sync changes to both agents/ and agent/ folders
- ALWAYS run on GitHub Actions not locally for production
- Groq free tier: 100K tokens/day, resets daily
