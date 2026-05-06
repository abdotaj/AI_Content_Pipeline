"""run_full.py — FULL PIPELINE entry point.

Sets PIPELINE_MODE=full before any agent import so agents/video_agent.py
enables full clip scoring, cinematic timeline, and run_full_pipeline().

Delegates all logic to run_darkcrimed.run_pipeline() via pipelines/full_pipeline.py.
"""
import os
os.environ["PIPELINE_MODE"] = "full"   # must be set before any agent import

from pipelines.full_pipeline import run_pipeline

if __name__ == "__main__":
    run_pipeline()
