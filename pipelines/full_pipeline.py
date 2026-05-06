# pipelines/full_pipeline.py
#
# FULL PIPELINE — delegates entirely to run_darkcrimed.run_pipeline().
# All high-quality behaviour lives there:
#   deep research · full scripts · Netflix audio · cinematic clips ·
#   Whisper subtitles · image enhancement · quality processing
#
# PIPELINE_MODE=full is set by run_full.py before this module is imported,
# so agents/video_agent.py picks it up at module-load time and enables the
# full clip-scoring, 20-minute budget, and run_full_pipeline() path.

import os
import sys

# Guarantee the project root is on the path regardless of working directory
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Patch 'config' to dark-crime settings before any agent import
import config_darkcrimed
sys.modules.setdefault("config", config_darkcrimed)

from run_darkcrimed import run_pipeline  # noqa: F401  re-exported as the full entry
