"""run_fast_ar.py — Fast pipeline, Arabic only (test/isolation mode).

Sets PIPELINE_MODE=fast before any agent import, then runs a single-language
Arabic pipeline via pipelines/lang_pipeline.py.

SAFETY:
  - Does NOT modify run_fast.py or pipelines/fast_pipeline.py
  - Does NOT upload to YouTube
  - Sends output to Telegram with [LANG TEST | FAST | ARABIC] prefix
  - Failure here cannot affect existing production pipelines

Usage:
  python run_fast_ar.py
  PIPELINE_MODE=fast python run_fast_ar.py
"""
import os
os.environ["PIPELINE_MODE"] = "fast"   # must be set before any agent import

from pipelines.lang_pipeline import run_lang_pipeline

if __name__ == "__main__":
    run_lang_pipeline("arabic", "fast")
