"""run_animation.py — ANIMATION PIPELINE entry point.

Sets PIPELINE_MODE=animation before any agent import so the pipeline
uses create_animation_video() (character-centric motion clips via
D-ID, Runway, Luma, Kling, or Ken Burns enhanced still fallback)
instead of the stock-image slideshow pipeline.

All pipeline logic lives in pipelines/animation_pipeline.py.
"""
import os
os.environ["PIPELINE_MODE"] = "animation"   # must be set before any agent import

from pipelines.animation_pipeline import run_pipeline

if __name__ == "__main__":
    run_pipeline()
