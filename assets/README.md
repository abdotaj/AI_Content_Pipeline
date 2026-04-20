# assets/

Static assets used by the pipeline at runtime.

## music/

Background music tracks mixed under voice at -24 dB (volume=0.06).
Files are **auto-downloaded on first run** by `ensure_music_assets()` in `video_agent.py`.
Do **not** commit `.mp3` files — they are in `.gitignore`.

| File | Used for | Source |
|------|----------|--------|
| `documentary_long.mp3` | Long videos (15–18 min) | Pixabay — royalty-free, safe for monetization |
| `documentary_short.mp3` | Shorts / Reels (55–90 sec) | Pixabay — royalty-free, safe for monetization |

To replace a track: delete the file and drop your own `.mp3` in its place before running.
GitHub Actions caches `assets/music/` under key `music-assets-v1` to avoid re-downloading.
