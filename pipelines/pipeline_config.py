# pipelines/pipeline_config.py
#
# Mode-aware pipeline constants.
#
# PHILOSOPHY:
#   FULL  — maximum cinematic quality: heavy scoring, advanced enhancement,
#            exhaustive retries, Whisper subtitles, image enhancement,
#            quality post-processing. Slower — best possible result.
#   FAST  — optimized workflow: same storytelling standards, same script
#            quality, same ElevenLabs voices — just fewer redundant passes,
#            reduced retry loops, no blocking waits, lighter media selection.
#            Production-upload-ready. NOT a "test mode".
#
# The difference between FULL and FAST is OPTIMIZATION LEVEL, not quality.
# Both modes must produce professional Arabic documentary videos.
# Neither mode generates outputs under 10 minutes.
#
# PIPELINE_MODE must already be set via os.environ before this module loads.

import os

PIPELINE_MODE: str = os.getenv("PIPELINE_MODE", "fast").lower().strip()

# ── Speech rate ───────────────────────────────────────────────────────────────
WORDS_PER_MINUTE: int = 156      # Arabic narration pace (ElevenLabs)
WORDS_PER_MINUTE_EN: int = 163   # English TTS is slightly faster

# ── Target video durations ────────────────────────────────────────────────────
# Both modes: 10-15 min default long-form.
# FULL mode can be scaled to 30/60 min by raising SCRIPT_WORD_MAX.
TARGET_VIDEO_MINUTES_MIN: int = 10
TARGET_VIDEO_MINUTES_MAX: int = 15

# ── Script word targets ───────────────────────────────────────────────────────
# SAME for both modes — quality must not differ between FAST and FULL.
#
#   10 min × 156 WPM = 1,560 words  ← hard abort floor (never go below)
#   11.5 min × 156 WPM = 1,800 words ← preferred production minimum
#   16 min × 156 WPM = 2,500 words  ← ceiling
#
SCRIPT_WORD_FLOOR: int = 1_560    # hard abort threshold — below this = aborted
SCRIPT_WORD_MIN:   int = 1_800    # preferred minimum; warning if below
SCRIPT_WORD_MAX:   int = 2_500    # hard ceiling

# ── GitHub Actions job timeouts ───────────────────────────────────────────────
# FAST target: 10-30 min actual processing → 90 min timeout (safety margin)
# FULL target: 60-90 min actual processing → 180 min timeout (safety margin)
if PIPELINE_MODE == "fast":
    PIPELINE_TIMEOUT_MINUTES: int = 90
else:
    PIPELINE_TIMEOUT_MINUTES: int = 180

# ── Clip processing budget (seconds) ─────────────────────────────────────────
# Time budget for the clip scoring/fetching phase ONLY — not total pipeline.
# FAST: 25 min (lighter selection, no deep scoring)
# FULL: 30 min (exhaustive scoring + diversity algorithm)
if PIPELINE_MODE == "fast":
    MAX_CLIP_PROCESSING_SECONDS: float = 25 * 60
else:
    MAX_CLIP_PROCESSING_SECONDS: float = 30 * 60

# ── Network / media fetch timeout (seconds) ──────────────────────────────────
if PIPELINE_MODE == "fast":
    MEDIA_FETCH_TIMEOUT: int = 60    # skip slow sources; keep workflow moving
else:
    MEDIA_FETCH_TIMEOUT: int = 300   # FULL waits longer for high-quality media

# ── Retry budgets ─────────────────────────────────────────────────────────────
# FAST skips redundant retry loops; FULL retries for best possible result.
if PIPELINE_MODE == "fast":
    MAX_RETRIES: int = 1
else:
    MAX_RETRIES: int = 3
