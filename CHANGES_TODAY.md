# Changes — 2026-04-09

## Summary
Full pipeline overhaul: true crime niche, 12-min videos, web research, Arabic translation, short clips, topic memory.

---

## config.py
- Replaced `NICHES` list with 8 true crime series (Breaking Bad, Narcos, Money Heist, Peaky Blinders, Ozark, The Wire, Griselda, criminal psychology)
- Added `NICHE_WEIGHTS = [0.20, 0.20, 0.15, 0.15, 0.10, 0.10, 0.05, 0.05]`
- Changed `VIDEO_DURATION_SECONDS` from `45` → `720` (12 minutes)

---

## main.py
- Added `sys.stdout/stderr.reconfigure(encoding='utf-8')` to fix Windows charmap crash on Arabic text
- Changed `_save_log` to use `encoding="utf-8"` and `ensure_ascii=False`
- Added import of `research_series` and `mark_covered` from `agent.research_agent`
- Added Step 1b: calls `research_series(series)` for each topic before scripting; attaches result as `topic["research"]`
- Added `mark_covered(series, video_id)` call after each successful publish to prevent topic repetition

---

## agents/script_agent.py  &  agent/script_agent.py
- Added `title_format = "Dark Crime Decoded: {series} — {curiosity_hook}"`
- Rewrote `write_script()`: English only, max_tokens=8000, explicit word count with structured paragraph breakdown (hook/background/rise/events/downfall/aftermath/shocking/CTA)
- Added `translate_script(en_script)`: translates English script_data to Arabic via Groq (temperature=0.3), copies metadata — replaces independent Arabic generation
- Rewrote `write_scripts()`: generates English first, then translates to Arabic (no more separate independent Arabic scripts)
- Removed deepfake-specific logic and old niche references

---

## agents/video_agent.py  &  agent/video_agent.py
- Fixed moviepy v1→v2 API: `from moviepy import ...` (not `moviepy.editor`), `resized()`, `cropped()`, `subclipped()`, `with_audio()`
- Fixed Windows `WinError 32` temp file lock: explicit `temp_audiofile` path in `FINAL_DIR` + retry cleanup loop
- Replaced `build_search_query()` with series-specific mappings reading title+topic+niche:
  - Narcos/Escobar → `"crime cartel dark city night"`
  - Breaking Bad/Heisenberg → `"chemistry lab desert smoke dark"`
  - Money Heist → `"bank heist mask robbery dark"`
  - Peaky Blinders/Shelby → `"vintage dark street fog smoke"`
  - Ozark → `"lake night crime dark money"`
  - The Wire → `"city crime street night urban dark"`
  - Griselda/Blanco → `"crime cartel dark city night"`
  - Default → `"crime investigation detective dark night"`
- Added `SHORTS_DIR = "output/shorts/"` and `cut_short_clip()`: cuts first 55 seconds of each video, saves to `output/shorts/{video_id}_short.mp4`
- `create_video()` now calls `cut_short_clip()` after assembly and stores path in `script_data["short_clip_path"]`
- Changed fallback Pexels query from `"technology"` → `"crime investigation"`
- `fetch_stock_videos` count increased from 3 → 5

---

## agents/notify_agent.py  &  agent/notify_agent.py
- After sending main video preview, checks `script_data["short_clip_path"]` and sends a second Telegram message with the short clip attached
- Short clip caption: `"SHORT VERSION — post this to TikTok, Instagram Reels and YouTube Shorts\n\n{hashtags}"`

---

## agents/research_agent.py  &  agent/research_agent.py
Complete rewrite — no Anthropic API, uses DuckDuckGo + Groq only:

- `web_search(query)`: DuckDuckGo via `ddgs` package (falls back to `duckduckgo_search`)
- `COVERED_TOPICS_PATH = "output/covered_topics.json"` — tracks covered series
- `_load_covered()` / `_covered_series_set()`: load covered series set
- `mark_covered(series, video_id)`: appends to covered_topics.json after successful publish
- `discover_new_series()`: 4 DuckDuckGo queries → Groq extracts 30 series titles → filters already-covered → returns up to 20 fresh
- `get_trending_topic(series, niche)`: Groq generates specific topic angle for a series
- `research_topics(count)`: calls `discover_new_series()`, filters covered, shuffles, picks `count` series, generates topic angles
- `research_series(series_name)`: 4 DuckDuckGo searches (real story, got wrong, shocking facts, real people) → Groq structures into research dict → injected into script prompt

---

## requirements.txt
- Added `beautifulsoup4>=4.12.0`
- Added `ddgs>=0.1.0`
- Added `anthropic>=0.40.0`
- Updated `moviepy==1.0.3` → `moviepy>=2.0.0`
- Updated `Pillow==9.5.0` → `Pillow>=9.5.0`

---

## Pipeline flow (updated)
```
main.py run_pipeline()
  0. listen_for_content (Telegram, 30s)
  1. ingest_content_files() OR:
     a. research_topics() → discover_new_series() + get_trending_topic()
     b. research_series() per topic → web facts via DuckDuckGo + Groq
  2. write_scripts() → write_script() (English) + translate_script() (Arabic)
  3. create_video() → voiceover → Pexels clips → assemble → cut_short_clip()
  4. send_video_preview() → sends main video + short clip to Telegram
     → wait_for_decision() (approve/skip, 5-min timeout → auto-approve)
     → publish_video() → mark_covered()
```

---

## Known issues / notes
- Groq llama-3.3-70b-versatile generates ~1100-1200 words despite 1656-word target; word count prompt improved with paragraph structure breakdown
- `research_series` for Anthropic API variant was also implemented but disabled (credits required) — falls back gracefully
- Windows charmap issue: fixed at stdout level; all file writes use `encoding="utf-8"`
