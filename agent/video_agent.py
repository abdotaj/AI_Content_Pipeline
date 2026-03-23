# ============================================================
#  agents/video_agent.py  —  Voiceover + footage + assembly
# ============================================================
import os
import requests
import random
from pathlib import Path
from config import (
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
    PEXELS_API_KEY, OUTPUT_DIR, AUDIO_DIR, VIDEO_DIR, FINAL_DIR,
    VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_DURATION_SECONDS
)

# Create output dirs
for d in [AUDIO_DIR, VIDEO_DIR, FINAL_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)


# ── 1. VOICEOVER ────────────────────────────────────────────

def generate_voiceover(script_text: str, filename: str) -> str:
    """Generate AI voiceover using ElevenLabs. Returns path to .mp3 file."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": script_text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.4,
            "use_speaker_boost": True
        }
    }
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()

    audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
    with open(audio_path, "wb") as f:
        f.write(response.content)

    print(f"[Video] Voiceover saved: {audio_path}")
    return audio_path


# ── 2. STOCK FOOTAGE ────────────────────────────────────────

def fetch_stock_videos(query: str, count: int = 5) -> list[str]:
    """Download stock video clips from Pexels. Returns list of file paths."""
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": count, "orientation": "portrait", "size": "medium"}

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    videos = response.json().get("videos", [])

    paths = []
    for i, video in enumerate(videos[:count]):
        # Pick best quality portrait file
        files = sorted(video["video_files"], key=lambda x: x.get("width", 0), reverse=True)
        portrait_files = [f for f in files if f.get("width", 0) <= 1080]
        if not portrait_files:
            portrait_files = files

        video_url = portrait_files[0]["link"]
        clip_path = os.path.join(VIDEO_DIR, f"clip_{i}.mp4")

        r = requests.get(video_url, stream=True)
        with open(clip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        paths.append(clip_path)
        print(f"[Video] Downloaded clip {i+1}/{count}")

    return paths


# ── 3. ASSEMBLY ─────────────────────────────────────────────

def assemble_video(
    audio_path: str,
    clip_paths: list[str],
    on_screen_texts: list[str],
    output_filename: str
) -> str:
    """
    Assembles final video using moviepy:
    - Stacks clips to match audio duration
    - Overlays on-screen text captions
    - Returns path to final .mp4
    """
    try:
        from moviepy.editor import (
            VideoFileClip, AudioFileClip, TextClip,
            CompositeVideoClip, concatenate_videoclips
        )
    except ImportError:
        print("[Video] moviepy not installed. Run: pip install moviepy")
        return ""

    audio = AudioFileClip(audio_path)
    total_duration = audio.duration

    # Loop/concatenate clips to fill audio duration
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

    base_video = concatenate_videoclips(assembled_clips, method="compose")
    base_video = base_video.set_audio(audio)

    # Add on-screen text overlays
    text_clips = [base_video]
    interval = total_duration / max(len(on_screen_texts), 1)

    for i, text in enumerate(on_screen_texts):
        start_time = i * interval
        end_time = min(start_time + interval - 0.5, total_duration)

        txt = TextClip(
            text,
            fontsize=60,
            color="white",
            font="Arial-Bold",
            stroke_color="black",
            stroke_width=2,
            method="caption",
            size=(VIDEO_WIDTH - 80, None)
        ).set_position(("center", 0.75), relative=True) \
         .set_start(start_time) \
         .set_end(end_time)

        text_clips.append(txt)

    final = CompositeVideoClip(text_clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final = final.set_duration(total_duration)

    output_path = os.path.join(FINAL_DIR, f"{output_filename}.mp4")
    final.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        logger=None
    )

    print(f"[Video] Final video: {output_path}")
    return output_path


# ── 4. FULL VIDEO PIPELINE ──────────────────────────────────

def create_video(script_data: dict, video_id: str) -> str:
    """Full pipeline: voiceover → footage → assembly. Returns final video path."""
    print(f"[Video] Starting: {script_data['title']}")

    # Step 1: Generate voiceover
    audio_path = generate_voiceover(script_data["script"], video_id)

    # Step 2: Fetch stock footage
    clip_paths = fetch_stock_videos(script_data["search_query"], count=5)
    if not clip_paths:
        print("[Video] No clips found, trying generic query")
        clip_paths = fetch_stock_videos("technology future", count=5)

    # Step 3: Assemble
    final_path = assemble_video(
        audio_path=audio_path,
        clip_paths=clip_paths,
        on_screen_texts=script_data.get("on_screen_texts", []),
        output_filename=video_id
    )

    return final_path
