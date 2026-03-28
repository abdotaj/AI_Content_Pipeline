# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Install dependencies:
```bash
pip install -r requirements.txt
```

Create a `.env` file (or export env vars) with the required keys — see `config.py` for the full list (Groq, Pexels, Telegram, YouTube, TikTok, etc.).

One-time YouTube OAuth setup:
```bash
python agents/publish_agent.py --auth-youtube
```
This generates `youtube_token.json` used for all future YouTube uploads.

## Running the Pipeline

```bash
python main.py
```

This executes the full daily pipeline (Research → Script → Video → Notify → Publish) and appends results to `output/publish_log.jsonl`.

## Architecture

The system is a **sequential 5-stage pipeline** for automated faceless short-form video creation targeting TikTok and YouTube Shorts. Each stage is a dedicated agent module in `agents/`.

```
main.py  (orchestrator: run_pipeline())
  │
  ├─ research_agent.py  →  Groq LLM picks a niche/topic, returns JSON
  ├─ script_agent.py    →  Groq LLM writes bilingual scripts (Arabic + English)
  │                         Each topic produces 2 script objects — one per language
  ├─ video_agent.py     →  edge-tts voiceover → Pexels stock clips → MoviePy assembly
  │                         Outputs 1080×1920 MP4 (vertical 9:16, libx264 ultrafast)
  ├─ notify_agent.py    →  Sends video preview to Telegram; polls for approve/skip
  │                         Auto-approves after 5-minute timeout
  └─ publish_agent.py   →  English → YouTube Shorts, Arabic → TikTok
```

**Key design decisions:**
- All configuration (API keys, niches, video dimensions, output paths) lives in `config.py`.
- A single topic always generates two videos: one Arabic (→ TikTok) and one English (→ YouTube).
- The Telegram approval gate is the only human-in-the-loop step; everything else is fully automated.
- Pipeline is designed to be triggered by an external scheduler (cron, GitHub Actions); there is no built-in scheduler.
- Failures emit a Telegram notification and log to `output/publish_log.jsonl`.

## LLM Usage

Both `research_agent.py` and `script_agent.py` use the **Groq API** with model `llama-3.3-70b-versatile`. All LLM responses are expected as JSON; parsing errors should surface the raw response for debugging.

## No Test Suite

There is no test framework configured. Manual testing means running `python main.py` and inspecting Telegram messages and `output/publish_log.jsonl`.
