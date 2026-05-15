"""run_animation_en.py — Animation pipeline, English only (test/isolation mode).

Sets PIPELINE_MODE=animation before any agent import, then runs a single-language
English pipeline via pipelines/lang_pipeline.py.

In animation mode the English script uses write_animation_script() (fact-anchored
cinematic 4000+ word script) and rendering uses create_animation_video()
from agent/animation_agent.py.

SAFETY:
  - Does NOT modify run_animation.py or pipelines/animation_pipeline.py
  - Does NOT upload to YouTube
  - Sends output to Telegram with [LANG TEST | ANIMATION | ENGLISH] prefix
  - Failure here cannot affect existing production pipelines

Usage:
  python run_animation_en.py
  PIPELINE_MODE=animation python run_animation_en.py
"""
import os
os.environ["PIPELINE_MODE"] = "animation"   # must be set before any agent import

from pipelines.lang_pipeline import run_lang_pipeline

if __name__ == "__main__":
    run_lang_pipeline("english", "animation")
