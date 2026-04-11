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


def generate_voiceover_edgetts(script_text: str, filename: str, language: str = "english") -> str:
    """Generate voiceover using edge-tts."""
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
    print(f"[Video] Voiceover saved (edge-tts): {audio_path}")
    return audio_path


def generate_voiceover(script_text: str, filename: str, language: str = "english") -> str:
    """Generate voiceover — ElevenLabs if configured, edge-tts as fallback."""
    from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID_EN, ELEVENLABS_VOICE_ID_AR, ELEVENLABS_VOICE_ID

    api_key = ELEVENLABS_API_KEY
    if not api_key or api_key == "YOUR_ELEVENLABS_KEY":
        return generate_voiceover_edgetts(script_text, filename, language)

    voice_ids = {
        "english": ELEVENLABS_VOICE_ID_EN or ELEVENLABS_VOICE_ID,
        "arabic":  ELEVENLABS_VOICE_ID_AR or ELEVENLABS_VOICE_ID,
    }
    voice_id = voice_ids.get(language.lower(), ELEVENLABS_VOICE_ID)
    if not voice_id:
        return generate_voiceover_edgetts(script_text, filename, language)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    data = {
        "text": script_text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.5,
            "use_speaker_boost": True,
        },
    }

    audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
    response = requests.post(url, json=data, headers=headers, timeout=120)
    if response.status_code == 200:
        with open(audio_path, "wb") as f:
            f.write(response.content)
        print(f"[Video] Voiceover saved (ElevenLabs): {audio_path}")
        return audio_path

    print(f"[Voice] ElevenLabs failed ({response.status_code}) — falling back to edge-tts")
    return generate_voiceover_edgetts(script_text, filename, language)


_BLOCKED_KEYWORDS = {
    "gun", "weapon", "knife", "blood", "murder", "kill",
    "violence", "crime scene", "body",
}


def _is_safe_clip(video: dict) -> bool:
    """Return False if the clip's tags/description contain blocked keywords."""
    text = " ".join([
        video.get("url", ""),
        " ".join(t.get("title", "") for t in video.get("tags", [])),
    ]).lower()
    return not any(kw in text for kw in _BLOCKED_KEYWORDS)


def fetch_stock_videos(query: str, count: int = 3) -> list[str]:
    """Download up to `count` safe clips — skips any with flagged keywords."""
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    # Request extra results so we have headroom after filtering
    params = {"query": query, "per_page": min(count * 3, 20), "orientation": "portrait", "size": "small"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        videos = response.json().get("videos", [])
    except Exception as e:
        print(f"[Video] Pexels error: {e}")
        return []

    paths = []
    for video in videos:
        if len(paths) >= count:
            break
        if not _is_safe_clip(video):
            print(f"[Video] Skipped flagged clip: {video.get('url', '')}")
            continue
        files = sorted(video["video_files"], key=lambda x: x.get("width", 0))
        video_url = files[0]["link"]
        clip_path = os.path.join(VIDEO_DIR, f"clip_{len(paths)}.mp4")
        try:
            r = requests.get(video_url, stream=True, timeout=20)
            with open(clip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            paths.append(clip_path)
            print(f"[Video] Downloaded clip {len(paths)}/{count}")
        except Exception as e:
            print(f"[Video] Clip download failed: {e}")
    return paths


def assemble_video(
    audio_path: str,
    clip_paths: list[str],
    output_filename: str,
) -> str:
    """Fast assembly — no text overlays, just video + audio."""
    try:
        from moviepy import VideoFileClip, AudioFileClip, concatenate_videoclips
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
            clip = clip.resized(height=VIDEO_HEIGHT)
            if clip.w > VIDEO_WIDTH:
                x_center = clip.w / 2
                clip = clip.cropped(
                    x1=x_center - VIDEO_WIDTH / 2,
                    x2=x_center + VIDEO_WIDTH / 2
                )

            remaining = total_duration - current_duration
            if clip.duration > remaining:
                clip = clip.subclipped(0, remaining)

            assembled_clips.append(clip)
            current_duration += clip.duration
            clip_index += 1

        final = concatenate_videoclips(assembled_clips, method="compose")
        final = final.with_audio(audio)

        output_path = os.path.join(FINAL_DIR, f"{output_filename}.mp4")
        temp_audio  = os.path.join(FINAL_DIR, f"{output_filename}_tmp_audio.m4a")
        final.write_videofile(
            output_path, fps=24, codec="libx264",
            audio_codec="aac", threads=4,
            preset="ultrafast",
            temp_audiofile=temp_audio,
            logger=None
        )
        # Clean up temp audio (Windows sometimes holds a lock briefly)
        import time
        for _ in range(5):
            try:
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                break
            except OSError:
                time.sleep(0.5)
        print(f"[Video] Final video: {output_path}")
        return output_path

    except Exception as e:
        print(f"[Video] Assembly error: {e}")
        return ""


def clean_voice(input_path: str, output_path: str) -> str:
    """
    Enhance a recorded voice file:
      1. Convert OGG → WAV via ffmpeg
      2. Noise reduction via noisereduce (first 0.5 s as noise profile)
      3. Apply ffmpeg audio filters (highpass, lowpass, denoiser, normalization)
      4. Output as MP3
    Returns output_path on success, or input_path if enhancement fails.
    """
    import subprocess
    import tempfile

    wav_path  = output_path.replace(".mp3", "_raw.wav")
    clean_wav = output_path.replace(".mp3", "_clean.wav")

    # Step 1: decode to WAV
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, wav_path],
            check=True, capture_output=True
        )
    except Exception as e:
        print(f"[Voice] ffmpeg decode failed: {e} — skipping enhancement")
        return input_path

    # Step 2: noise reduction
    try:
        import noisereduce as nr
        import soundfile as sf
        import numpy as np

        data, rate = sf.read(wav_path)
        noise_sample = data[:int(rate * 0.5)]
        reduced = nr.reduce_noise(
            y=data, sr=rate, y_noise=noise_sample,
            prop_decrease=0.75, stationary=False
        )
        sf.write(clean_wav, reduced, rate)
    except Exception as e:
        print(f"[Voice] Noise reduction failed: {e} — using raw WAV")
        clean_wav = wav_path

    # Step 3: ffmpeg audio filters + encode to MP3
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", (
                    "highpass=f=80,"
                    "lowpass=f=8000,"
                    "anlmdn=s=7:p=0.002:r=0.002,"
                    "dynaudnorm=p=0.9"
                ),
                "-ar", "44100",
                output_path,
            ],
            check=True, capture_output=True
        )
        print(f"[Voice] Enhanced audio saved: {output_path}")
    except Exception as e:
        print(f"[Voice] ffmpeg filter failed: {e} — using unfiltered input")
        return input_path

    # Clean up intermediates
    for f in [wav_path, clean_wav]:
        try:
            if os.path.exists(f) and f != output_path:
                os.remove(f)
        except OSError:
            pass

    return output_path


SHORTS_DIR = "output/shorts"
Path(SHORTS_DIR).mkdir(parents=True, exist_ok=True)


def cut_short_clip(video_path: str, video_id: str, duration: int = 55) -> str:
    """Cut the first `duration` seconds of a video and save to output/shorts/."""
    try:
        from moviepy import VideoFileClip
    except ImportError:
        return ""

    short_path = os.path.join(SHORTS_DIR, f"{video_id}_short.mp4")
    temp_audio  = os.path.join(SHORTS_DIR, f"{video_id}_short_tmp_audio.m4a")
    try:
        clip = VideoFileClip(video_path)
        end = min(duration, clip.duration)
        short = clip.subclipped(0, end)
        short.write_videofile(
            short_path, fps=24, codec="libx264",
            audio_codec="aac", threads=4, preset="ultrafast",
            temp_audiofile=temp_audio, logger=None
        )
        clip.close()
        import time
        for _ in range(5):
            try:
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                break
            except OSError:
                time.sleep(0.5)
        print(f"[Video] Short clip saved: {short_path}")
        return short_path
    except Exception as e:
        print(f"[Video] Short clip error: {e}")
        return ""


def build_search_query(script_data: dict) -> str:
    combined = " ".join([
        script_data.get("title", ""),
        script_data.get("topic", ""),
        script_data.get("niche", ""),
    ]).lower()

    if any(w in combined for w in ["narcos", "escobar", "pablo", "colombia", "cartel"]):
        return "colombia city documentary archive"
    if any(w in combined for w in ["breaking bad", "walter white", "heisenberg", "meth"]):
        return "chemistry laboratory science research"
    if any(w in combined for w in ["money heist", "la casa", "bella ciao", "heist", "robbery"]):
        return "bank building architecture city"
    if any(w in combined for w in ["peaky blinders", "tommy shelby", "shelby", "birmingham"]):
        return "vintage 1920s city street historical"
    if any(w in combined for w in ["ozark", "byrde", "money laundering"]):
        return "lake nature landscape missouri"
    if any(w in combined for w in ["the wire", "baltimore", "drug trade"]):
        return "city street urban documentary"
    if any(w in combined for w in ["scarface", "tony montana"]):
        return "miami city night skyline"
    if any(w in combined for w in ["griselda", "blanco", "cocaine", "miami"]):
        return "miami documentary city lights"
    if any(w in combined for w in ["dahmer", "monster", "serial killer"]):
        return "courtroom justice newspaper archive"

    return "documentary film city lights archive"


def create_video(script_data: dict, video_id: str, custom_audio_path: str = "") -> str:
    language = script_data.get("language", "english")
    print(f"[Video] Starting: {script_data['title']} ({language})")

    if custom_audio_path and Path(custom_audio_path).exists():
        enhanced_path = os.path.join(AUDIO_DIR, f"{video_id}_enhanced.mp3")
        audio_path = clean_voice(custom_audio_path, enhanced_path)
        print(f"[Video] Using custom audio: {audio_path}")
    else:
        audio_path = generate_voiceover(script_data["script"], video_id, language)

    query = build_search_query(script_data)
    print(f"[Video] Pexels query: '{query}'")
    clip_paths = fetch_stock_videos(query, count=5)
    if not clip_paths:
        clip_paths = fetch_stock_videos("documentary film city lights archive", count=5)

    if not clip_paths:
        print("[Video] No clips found, skipping")
        return ""

    video_path = assemble_video(
        audio_path=audio_path,
        clip_paths=clip_paths,
        output_filename=video_id
    )
    if video_path:
        script_data["short_clip_path"] = cut_short_clip(video_path, video_id)
    return video_path
