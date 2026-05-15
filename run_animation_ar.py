"""run_animation_ar.py — Animation pipeline, Arabic only (test/isolation mode).

Sets PIPELINE_MODE=animation before any agent import, then runs a single-language
Arabic pipeline via pipelines/lang_pipeline.py.

In animation mode the Arabic script uses write_arabic_script() (native Arabic,
no English translation dependency) and rendering uses create_animation_video()
from agent/animation_agent.py.

SAFETY:
  - Does NOT modify run_animation.py or pipelines/animation_pipeline.py
  - Does NOT upload to YouTube
  - Sends output to Telegram with [LANG TEST | ANIMATION | ARABIC] prefix
  - Failure here cannot affect existing production pipelines

Usage:
  python run_animation_ar.py
  PIPELINE_MODE=animation python run_animation_ar.py
"""
import os
os.environ["PIPELINE_MODE"] = "animation"   # must be set before any agent import

from pipelines.lang_pipeline import run_lang_pipeline

if __name__ == "__main__":
    run_lang_pipeline("arabic", "animation")
