# ============================================================
#  agents/video_agent.py  —  AI-generated images + voiceover
# ============================================================
import os
import time
import asyncio
import requests
from pathlib import Path
from config import (
    AUDIO_DIR, VIDEO_DIR, FINAL_DIR,
    VIDEO_WIDTH, VIDEO_HEIGHT
)

IMAGES_DIR = "output/images"
for d in [AUDIO_DIR, VIDEO_DIR, FINAL_DIR, IMAGES_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)


# ── Voiceover ─────────────────────────────────────────────────────────────────

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


def _get_ffmpeg() -> str:
    """Locate ffmpeg binary, checking PATH and common Windows install locations."""
    import shutil as _shutil
    candidates = [
        _shutil.which("ffmpeg"),
        "C:/ffmpeg/bin/ffmpeg.exe",
        "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
        "C:/Users/abdot/AppData/Local/Programs/ffmpeg/bin/ffmpeg.exe",
    ]
    for loc in candidates:
        if loc and os.path.exists(loc):
            return loc
    try:
        from moviepy.config import get_setting
        return get_setting("FFMPEG_BINARY")
    except Exception:
        pass
    return "ffmpeg"


def _split_text(text: str, max_chars: int = 2500) -> list[str]:
    """Split text on word boundaries into chunks no larger than max_chars."""
    chunks: list[str] = []
    words = text.split()
    current: list[str] = []
    current_len = 0
    for word in words:
        if current_len + len(word) > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def _elevenlabs_chunk(chunk: str, voice_id: str, api_key: str, chunk_path: str) -> bool:
    """POST one chunk to ElevenLabs. Returns True on success."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    data = {
        "text": chunk,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.85,
            "style": 0.4,
            "use_speaker_boost": True,
        },
    }
    try:
        response = requests.post(url, json=data, headers=headers, timeout=60)
        if response.status_code == 200:
            with open(chunk_path, "wb") as f:
                f.write(response.content)
            return True
        print(f"[Voice] ElevenLabs chunk failed: {response.status_code}")
    except Exception as e:
        print(f"[Voice] ElevenLabs chunk error: {e}")
    return False


def generate_voiceover(script_text: str, filename: str, language: str = "english") -> str:
    """Generate voiceover — ElevenLabs (chunked) if configured, edge-tts as fallback."""
    from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID_EN, ELEVENLABS_VOICE_ID_AR, ELEVENLABS_VOICE_ID

    api_key = ELEVENLABS_API_KEY
    print(f"[Voice] ElevenLabs key: {'set' if api_key and api_key != 'YOUR_ELEVENLABS_KEY' else 'MISSING'}")
    if not api_key or api_key == "YOUR_ELEVENLABS_KEY":
        return generate_voiceover_edgetts(script_text, filename, language)

    voice_ids = {
        "english": ELEVENLABS_VOICE_ID_EN or ELEVENLABS_VOICE_ID,
        "arabic":  ELEVENLABS_VOICE_ID_AR or ELEVENLABS_VOICE_ID,
    }
    voice_id = voice_ids.get(language.lower(), ELEVENLABS_VOICE_ID)
    print(f"[Voice] Voice ID ({language}): {'set' if voice_id else 'MISSING'}")
    if not voice_id:
        return generate_voiceover_edgetts(script_text, filename, language)

    audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
    chunks = _split_text(script_text, max_chars=2500)
    print(f"[Voice] ElevenLabs: {len(chunks)} chunk(s) for {language}")

    chunk_files: list[str] = []
    for i, chunk in enumerate(chunks):
        chunk_path = os.path.join(AUDIO_DIR, f"{filename}_chunk_{i}.mp3")
        if _elevenlabs_chunk(chunk, voice_id, api_key, chunk_path):
            chunk_files.append(chunk_path)
            print(f"[Voice] Chunk {i + 1}/{len(chunks)} done")
            if i < len(chunks) - 1:
                time.sleep(2)
        else:
            # Clean up and fall back to edge-tts for the whole script
            for f in chunk_files:
                try: os.remove(f)
                except OSError: pass
            print(f"[Voice] Chunk {i + 1} failed — falling back to edge-tts")
            return generate_voiceover_edgetts(script_text, filename, language)

    if len(chunk_files) == 1:
        import shutil
        shutil.move(chunk_files[0], audio_path)
    else:
        merged = False

        # Attempt 1 — ffmpeg concat
        import subprocess
        list_path = os.path.join(AUDIO_DIR, f"{filename}_list.txt")
        with open(list_path, "w", encoding="utf-8") as lf:
            for cf in chunk_files:
                lf.write(f"file '{os.path.abspath(cf)}'\n")
        try:
            ffmpeg_bin = _get_ffmpeg()
            subprocess.run(
                [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", audio_path],
                check=True, capture_output=True,
            )
            merged = True
            print("[Voice] Chunks merged with ffmpeg")
        except Exception as e:
            print(f"[Voice] ffmpeg concat failed: {e}")

        # Attempt 2 — pydub
        if not merged:
            try:
                from pydub import AudioSegment
                combined = AudioSegment.empty()
                for cf in chunk_files:
                    combined += AudioSegment.from_mp3(cf)
                combined.export(audio_path, format="mp3")
                merged = True
                print("[Voice] Chunks merged with pydub")
            except Exception as e:
                print(f"[Voice] pydub merge failed: {e}")

        # Attempt 3 — use first chunk only (better than silence)
        if not merged:
            import shutil
            shutil.copy(chunk_files[0], audio_path)
            print("[Voice] Using first chunk only (merge failed)")

        # Cleanup
        for f in chunk_files:
            try: os.remove(f)
            except OSError: pass
        try: os.remove(list_path)
        except OSError: pass

    print(f"[Voice] ElevenLabs complete: {len(chunks)} chunk(s) → {audio_path}")
    return audio_path


# ── AI Image generation (Pollinations — free, no key) ─────────────────────────

def generate_image_prompts(title: str, niche: str, script: str = "") -> list[str]:
    """Build 5 cinematic image prompts from the video title/niche."""
    prompts = []
    t = title.lower()

    # Prompt 1 — Main subject/setting
    if "dexter" in t:
        prompts.append("forensic detective crime lab miami dark cinematic 2006")
    elif "godfather" in t or "corleone" in t:
        prompts.append("1970s new york mafia don dark cinematic dramatic")
    elif "scarface" in t or "tony montana" in t:
        prompts.append("miami 1980s drug lord mansion cinematic dark")
    elif "narcos" in t or "escobar" in t:
        prompts.append("colombia 1980s cartel boss jungle cinematic")
    elif "breaking bad" in t or "walter white" in t:
        prompts.append("chemistry lab desert new mexico dark cinematic")
    elif "money heist" in t:
        prompts.append("bank vault heist masked robbers spain cinematic")
    elif "peaky blinders" in t:
        prompts.append("1920s birmingham england gang dark smoke cinematic")
    elif "goodfellas" in t:
        prompts.append("1970s new york italian mafia restaurant cinematic")
    elif "casino" in t:
        prompts.append("1970s las vegas casino mafia dark cinematic")
    elif "ozark" in t:
        prompts.append("dark lake missouri night money laundering cinematic")
    elif "the wire" in t or "baltimore" in t:
        prompts.append("baltimore city street night crime documentary")
    elif "griselda" in t:
        prompts.append("miami 1970s female drug lord cinematic dark")
    elif "city of god" in t:
        prompts.append("brazil favela 1980s crime documentary cinematic")
    elif "sicario" in t:
        prompts.append("mexico border desert cartel cinematic dark")
    elif "american gangster" in t:
        prompts.append("1970s harlem new york drug lord cinematic")
    else:
        prompts.append("true crime documentary dark cinematic investigation")

    # Prompts 2-5 — universal cinematic beats
    prompts.append("detective investigation evidence board crime cinematic dark")
    prompts.append("documentary style crime scene investigation cinematic")
    prompts.append("vintage newspaper headline crime story archive dramatic")
    prompts.append("courtroom justice verdict dramatic cinematic dark")

    return prompts


def generate_ai_image(prompt: str, output_path: str, width: int = 1080, height: int = 1920, retries: int = 3) -> str:
    """Fetch an AI-generated image from Pollinations with retry + dark fallback."""
    encoded = requests.utils.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true"

    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=120)
            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                print(f"[Image] Generated: {prompt[:60]}")
                return output_path
            elif response.status_code == 429:
                print(f"[Image] Rate limited, waiting 30s... (attempt {attempt + 1}/{retries})")
                time.sleep(30)
            else:
                print(f"[Image] Pollinations returned {response.status_code} (attempt {attempt + 1}/{retries})")
                time.sleep(10)
        except Exception as e:
            print(f"[Image] Attempt {attempt + 1} failed: {e}")
            time.sleep(15)

    # Fallback: solid dark background so assembly never crashes
    from PIL import Image as PILImage
    img = PILImage.new("RGB", (width, height), color=(13, 13, 26))
    img.save(output_path)
    print(f"[Image] Using dark background fallback for: {prompt[:60]}")
    return output_path


# ── MoviePy clip helpers ───────────────────────────────────────────────────────

def image_to_clip(image_path: str, duration: int = 4):
    """Still image → video clip with Ken Burns zoom + fade in/out."""
    from moviepy import ImageClip
    from moviepy.video.fx import FadeIn, FadeOut

    clip = ImageClip(image_path, duration=duration)
    clip = clip.resized(lambda t: 1 + 0.03 * t)
    clip = clip.with_effects([FadeIn(0.5), FadeOut(0.5)])
    return clip


def _detect_font() -> str | None:
    """Return the first usable font path/name for TextClip on this system."""
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        "DejaVu-Sans-Bold",
        "Arial-Bold",
        "Arial",
    ]
    from moviepy import TextClip as _TC
    for font in candidates:
        try:
            _TC(font=font, text="test", font_size=20).close()
            return font
        except Exception:
            continue
    return None


def add_text_overlay(clip, text: str, position: str = "top"):
    """Burn a text label onto a clip. Returns original clip on failure."""
    try:
        from moviepy import TextClip, CompositeVideoClip

        font = _detect_font()
        if font is None:
            print("[Video] No usable font found — text overlay skipped")
            return clip

        txt = (
            TextClip(
                font=font,
                text=text,
                font_size=45,
                color="white",
                stroke_color="black",
                stroke_width=2,
                method="caption",
                size=(clip.w - 80, None),
            )
            .with_duration(clip.duration)
        )
        y_pos = 50 if position == "top" else clip.h - 200
        txt = txt.with_position(("center", y_pos))
        return CompositeVideoClip([clip, txt])
    except Exception as e:
        print(f"[Video] Text overlay skipped: {e}")
        return clip


def assemble_video(audio_path: str, image_clips: list, output_filename: str) -> str:
    """Loop image clips to cover the full audio duration, mux, and export."""
    from moviepy import AudioFileClip, concatenate_videoclips

    try:
        audio = AudioFileClip(audio_path)
        total_duration = audio.duration

        looped: list = []
        accumulated = 0.0
        idx = 0
        while accumulated < total_duration:
            clip = image_clips[idx % len(image_clips)]
            remaining = total_duration - accumulated
            if clip.duration > remaining:
                clip = clip.subclipped(0, remaining)
            looped.append(clip)
            accumulated += clip.duration
            idx += 1

        final = concatenate_videoclips(looped, method="compose")
        final = final.with_audio(audio)

        output_path = os.path.join(FINAL_DIR, f"{output_filename}.mp4")
        temp_audio  = os.path.join(FINAL_DIR, f"{output_filename}_tmp_audio.m4a")
        final.write_videofile(
            output_path, fps=24, codec="libx264",
            audio_codec="aac", threads=4, preset="ultrafast",
            temp_audiofile=temp_audio, logger=None,
        )
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


# ── Voice enhancement (for user-recorded audio) ───────────────────────────────

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

    wav_path  = output_path.replace(".mp3", "_raw.wav")
    clean_wav = output_path.replace(".mp3", "_clean.wav")

    try:
        subprocess.run(["ffmpeg", "-y", "-i", input_path, wav_path], check=True, capture_output=True)
    except Exception as e:
        print(f"[Voice] ffmpeg decode failed: {e} — skipping enhancement")
        return input_path

    try:
        import noisereduce as nr
        import soundfile as sf
        data, rate = sf.read(wav_path)
        noise_sample = data[:int(rate * 0.5)]
        reduced = nr.reduce_noise(y=data, sr=rate, y_noise=noise_sample, prop_decrease=0.75, stationary=False)
        sf.write(clean_wav, reduced, rate)
    except Exception as e:
        print(f"[Voice] Noise reduction failed: {e} — using raw WAV")
        clean_wav = wav_path

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", clean_wav,
             "-af", "highpass=f=80,lowpass=f=8000,anlmdn=s=7:p=0.002:r=0.002,dynaudnorm=p=0.9",
             "-ar", "44100", output_path],
            check=True, capture_output=True,
        )
        print(f"[Voice] Enhanced audio saved: {output_path}")
    except Exception as e:
        print(f"[Voice] ffmpeg filter failed: {e} — using unfiltered input")
        return input_path

    for f in [wav_path, clean_wav]:
        try:
            if os.path.exists(f) and f != output_path:
                os.remove(f)
        except OSError:
            pass

    return output_path


# ── Short clip cutter ──────────────────────────────────────────────────────────

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
            temp_audiofile=temp_audio, logger=None,
        )
        clip.close()
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


# ── Main entry point ───────────────────────────────────────────────────────────

def create_video(script_data: dict, video_id: str, custom_audio_path: str = "") -> str:
    title    = script_data.get("title", "")
    niche    = script_data.get("niche", "")
    language = script_data.get("language", "english")
    print(f"[Video] Starting: {title} ({language})")

    # Voiceover
    if custom_audio_path and Path(custom_audio_path).exists():
        enhanced_path = os.path.join(AUDIO_DIR, f"{video_id}_enhanced.mp3")
        audio_path = clean_voice(custom_audio_path, enhanced_path)
        print(f"[Video] Using custom audio: {audio_path}")
    else:
        audio_path = generate_voiceover(script_data["script"], video_id, language)

    # Generate AI images via Pollinations
    prompts = generate_image_prompts(title, niche, script_data.get("script", ""))
    image_clips = []
    for i, prompt in enumerate(prompts):
        img_path = os.path.join(IMAGES_DIR, f"{video_id}_img_{i}.jpg")
        result = generate_ai_image(prompt, img_path)
        if result:
            clip = image_to_clip(result, duration=4)
            clip = add_text_overlay(clip, "Dark Crime Decoded", "top")
            image_clips.append(clip)
        time.sleep(5)  # respect Pollinations rate limit

    if not image_clips:
        print("[Video] No images generated, skipping")
        return ""

    video_path = assemble_video(
        audio_path=audio_path,
        image_clips=image_clips,
        output_filename=video_id,
    )
    if video_path:
        script_data["short_clip_path"] = cut_short_clip(video_path, video_id)
    return video_path
