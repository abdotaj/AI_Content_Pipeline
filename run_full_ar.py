"""run_full_ar.py — Full pipeline, Arabic only (test/isolation mode).

Sets PIPELINE_MODE=full before any agent import, then runs a single-language
Arabic pipeline via pipelines/lang_pipeline.py.

SAFETY:
  - Does NOT modify run_full.py or pipelines/full_pipeline.py
  - Does NOT upload to YouTube
  - Sends output to Telegram with [LANG TEST | FULL | ARABIC] prefix
  - Failure here cannot affect existing production pipelines

Usage:
  python run_full_ar.py
  PIPELINE_MODE=full python run_full_ar.py
"""
import os
os.environ["PIPELINE_MODE"] = "full"   # must be set before any agent import

from pipelines.lang_pipeline import run_lang_pipeline

if __name__ == "__main__":
    run_lang_pipeline("arabic", "full")
