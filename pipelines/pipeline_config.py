# pipelines/pipeline_config.py
#
# Mode-aware pipeline constants.  Import these everywhere instead of
# hardcoding timeout / word-count / retry values in individual files.
#
# Usage:
#   from pipelines.pipeline_config import (
#       WORDS_PER_MINUTE,
#       SCRIPT_WORD_MIN, SCRIPT_WORD_MAX,
#       PIPELINE_TIMEOUT_MINUTES, MEDIA_FETCH_TIMEOUT,
#       MAX_RETRIES,
#   )
#
# PIPELINE_MODE must already be set via os.environ before this module loads.

import os

PIPELINE_MODE: str = os.getenv("PIPELINE_MODE", "fast").lower().strip()

# ── Speech rate ───────────────────────────────────────────────────────────────
WORDS_PER_MINUTE: int = 156          # documentary narration pace (ElevenLabs)
WORDS_PER_MINUTE_EN: int = 163      # English TTS is slightly faster

# ── Target video durations (minutes) ─────────────────────────────────────────
# Both modes target 10-15 min long-form; FULL can be scaled to 30/60 min by
# raising SCRIPT_WORD_MAX externally without touching pipeline logic.
TARGET_VIDEO_MINUTES_MIN: int = 10
TARGET_VIDEO_MINUTES_MAX: int = 15

# ── Script word targets (derived from duration targets @ WORDS_PER_MINUTE) ───
# 10 min × 156 WPM = 1,560 words  (hard floor — never go below this)
# 15 min × 156 WPM = 2,340 words  (FAST ceiling)
# 16 min × 156 WPM = 2,500 words  (FULL ceiling — existing value preserved)

if PIPELINE_MODE == "fast":
    SCRIPT_WORD_MIN: int = 1_560   # 10 min floor — enforced in fast_pipeline.py
    SCRIPT_WORD_MAX: int = 2_340   # 15 min ceiling
else:
    SCRIPT_WORD_MIN: int = 1_800   # 11.5 min floor (existing FULL value)
    SCRIPT_WORD_MAX: int = 2_500   # 16 min ceiling (existing FULL value)

# ── GitHub Actions job timeouts (minutes) ────────────────────────────────────
# These are informational / used for documentation.  The actual timeout is set
# in the .github/workflows/*.yml files.
if PIPELINE_MODE == "fast":
    PIPELINE_TIMEOUT_MINUTES: int = 120   # realistic: TTS×4 + assembly×4 + upload
else:
    PIPELINE_TIMEOUT_MINUTES: int = 480   # FULL: Whisper + enhancement + retries

# ── Clip processing budget (seconds) ─────────────────────────────────────────
# Time allowed for the video clip scoring / fetching phase only.
# NOT total pipeline time.
if PIPELINE_MODE == "fast":
    MAX_CLIP_PROCESSING_SECONDS: float = 25 * 60   # 25 min — realistic for FAST
else:
    MAX_CLIP_PROCESSING_SECONDS: float = 30 * 60   # 30 min — FULL has more sources

# ── Network / media fetch timeout (seconds) ──────────────────────────────────
if PIPELINE_MODE == "fast":
    MEDIA_FETCH_TIMEOUT: int = 60    # 1-min cap per source fetch
else:
    MEDIA_FETCH_TIMEOUT: int = 300   # 5-min cap per source fetch (Pexels video DL)

# ── Retry budgets ─────────────────────────────────────────────────────────────
if PIPELINE_MODE == "fast":
    MAX_RETRIES: int = 1   # fail fast — no blocking waits
else:
    MAX_RETRIES: int = 3   # FULL mode retries transient failures
