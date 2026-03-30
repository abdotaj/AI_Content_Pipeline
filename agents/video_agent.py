# ============================================================
#  agents/video_agent.py  —  Fast video assembly (no ImageMagick)
# ============================================================
import os
import asyncio
import requests
from pathlib import Path
from config import (
    PEXELS_API_KEY, AUDIO_DIR, VIDEO_DIR, FINAL_DIR,
    VIDEO_WIDTH, VIDEO_HEIGHT
)

for d in [AUDIO_DIR, VIDEO_DIR, FINAL_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)


def get_voice(language: str) -> str:
    voices = {
        "arabic": "ar-SA-HamedNeural",
        "english": "en-US-GuyNeural"
    }
    return voices.get(language.lower(), "en-US-GuyNeural")


def generate_voiceover(script_text: str, filename: str, language: str = "english") -> str:
    try:
        import edge_tts
    except ImportError:
        os.system("pip install edge-tts -q")
        import edge_tts

    voice = get_voice(language)
    audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")

    async def _generate():
        communicate = edge_tts.Communicate(script_text, voice)
        await communicate.save(audio_path)

    asyncio.run(_generate())
    print(f"[Video] Voiceover saved: {audio_path}")
    return audio_path


def fetch_stock_videos(query: str, count: int = 5) -> list[str]:
    """Download up to 5 clips for more variety."""
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": count, "orientation": "portrait", "size": "small"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        videos = response.json().get("videos", [])
    except Exception as e:
        print(f"[Video] Pexels error: {e}")
        return []

    paths = []
    for i, video in enumerate(videos[:count]):
        files = sorted(video["video_files"], key=lambda x: x.get("width", 0))
        # Pick smallest file for speed
        video_url = files[0]["link"]
        clip_path = os.path.join(VIDEO_DIR, f"clip_{i}.mp4")
        try:
            r = requests.get(video_url, stream=True, timeout=20)
            with open(clip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            paths.append(clip_path)
            print(f"[Video] Downloaded clip {i+1}/{count}")
        except Exception as e:
            print(f"[Video] Clip {i+1} failed: {e}")
    return paths


def assemble_video(
    audio_path: str,
    clip_paths: list[str],
    output_filename: str,
) -> str:
    """Fast assembly — no text overlays, just video + audio."""
    try:
        from moviepy.editor import (
            VideoFileClip, AudioFileClip,
            concatenate_videoclips
        )
    except ImportError:
        print("[Video] moviepy not installed.")
        return ""

    try:
        audio = AudioFileClip(audio_path)
        total_duration = audio.duration

        assembled_clips = []
        current_duration = 0
        clip_index = 0

        while current_duration < total_duration:
            clip_path = clip_paths[clip_index % len(clip_paths)]
            clip = VideoFileClip(clip_path).without_audio()

            # Resize to vertical 9:16
            clip = clip.resize(height=VIDEO_HEIGHT)
            if clip.w > VIDEO_WIDTH:
                x_center = clip.w / 2
                clip = clip.crop(
                    x1=x_center - VIDEO_WIDTH / 2,
                    x2=x_center + VIDEO_WIDTH / 2
                )

            remaining = total_duration - current_duration
            if clip.duration > remaining:
                clip = clip.subclip(0, remaining)

            assembled_clips.append(clip)
            current_duration += clip.duration
            clip_index += 1

        final = concatenate_videoclips(assembled_clips, method="compose")
        final = final.set_audio(audio)

        output_path = os.path.join(FINAL_DIR, f"{output_filename}.mp4")
        final.write_videofile(
            output_path, fps=24, codec="libx264",
            audio_codec="aac", threads=4,
            preset="ultrafast",  # fastest encoding
            logger=None
        )
        print(f"[Video] Final video: {output_path}")
        return output_path

    except Exception as e:
        print(f"[Video] Assembly error: {e}")
        return ""


def build_search_query(script_data: dict) -> str:
    topic    = script_data.get("topic", "").lower()
    keywords = script_data.get("keywords", [])

    if any(w in topic for w in ["black hole", "space", "galaxy", "star", "planet", "universe", "cosmos", "astronomy"]):
        return "space galaxy stars universe"

    if any(w in topic for w in ["deepfake", "fake", "detection", "scam", "fraud"]):
        return "face technology digital screen"

    if any(w in topic for w in ["music", "concert", "singer", "song"]):
        return "music concert performance stage"

    if any(w in topic for w in ["ai", "artificial intelligence", "robot", "tech", "digital"]):
        return "artificial intelligence technology computer"

    if any(w in topic for w in ["motivation", "success", "mindset", "goal", "morning"]):
        return "success motivation achievement"

    if any(w in topic for w in ["history", "ancient", "civilization", "war", "empire"]):
        return "ancient ruins civilization history"

    if any(w in topic for w in ["science", "discovery", "research", "experiment"]):
        return "science laboratory research"

    return script_data.get("search_query", keywords[0] if keywords else "technology")


def create_video(script_data: dict, video_id: str) -> str:
    language = script_data.get("language", "english")
    print(f"[Video] Starting: {script_data['title']} ({language})")

    audio_path = generate_voiceover(
        script_data["script"], video_id, language
    )

    query = build_search_query(script_data)
    print(f"[Video] Pexels query: '{query}'")
    clip_paths = fetch_stock_videos(query, count=5)
    if not clip_paths:
        clip_paths = fetch_stock_videos("technology", count=5)

    if not clip_paths:
        print("[Video] No clips found, skipping")
        return ""

    return assemble_video(
        audio_path=audio_path,
        clip_paths=clip_paths,
        output_filename=video_id
    )
