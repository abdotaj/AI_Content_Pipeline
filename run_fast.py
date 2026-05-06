"""run_fast.py — FAST PIPELINE entry point.

Sets PIPELINE_MODE=fast before any agent import so agents/video_agent.py
uses select_best_clips_fast() (no scoring) and run_fast_pipeline()
(no Whisper, no enhancement, no quality post-processing).

All pipeline logic lives in pipelines/fast_pipeline.py.
"""
import os
os.environ["PIPELINE_MODE"] = "fast"   # must be set before any agent import

from pipelines.fast_pipeline import run_pipeline

if __name__ == "__main__":
    run_pipeline()
