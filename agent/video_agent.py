# ============================================================
#  agents/video_agent.py  —  AI-generated images + voiceover
# ============================================================
import os
import time
import random
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


def _get_ffmpeg() -> str | None:
    """Locate ffmpeg binary — imageio_ffmpeg (bundled with moviepy) first."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    try:
        import shutil as _shutil
        path = _shutil.which("ffmpeg")
        if path:
            return path
    except Exception:
        pass
    for loc in [
        r"C:\Users\abdot\AppData\Roaming\Python\Python314\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe",
        "C:/ffmpeg/bin/ffmpeg.exe",
        "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
    ]:
        if os.path.exists(loc):
            return loc
    return None


def _merge_chunks_pydub(chunk_files: list[str], output_path: str) -> bool:
    """Merge MP3 chunks with pydub, pointing it at the imageio_ffmpeg binary."""
    try:
        ffmpeg_path = _get_ffmpeg()
        import pydub
        if ffmpeg_path:
            pydub.AudioSegment.converter = ffmpeg_path
        from pydub import AudioSegment
        combined = AudioSegment.empty()
        for cf in chunk_files:
            combined += AudioSegment.from_mp3(cf)
        combined.export(output_path, format="mp3")
        return True
    except Exception as e:
        print(f"[Voice] pydub merge failed: {e}")
        return False


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
        ffmpeg_bin = _get_ffmpeg()
        if ffmpeg_bin:
            try:
                subprocess.run(
                    [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", audio_path],
                    check=True, capture_output=True,
                )
                merged = True
                print("[Voice] Chunks merged with ffmpeg")
            except Exception as e:
                print(f"[Voice] ffmpeg concat failed: {e}")

        # Attempt 2 — pydub (with imageio_ffmpeg path injected)
        if not merged:
            if _merge_chunks_pydub(chunk_files, audio_path):
                merged = True
                print("[Voice] Chunks merged with pydub")

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

# Subject lookup: key → portrait prompt
_SUBJECTS = {
    # Real criminals
    "escobar":        "Pablo Escobar Colombian drug lord portrait cinematic dramatic",
    "pablo":          "Pablo Escobar Colombian drug lord portrait cinematic dramatic",
    "al capone":      "Al Capone Chicago gangster portrait 1920s cinematic",
    "capone":         "Al Capone Chicago gangster portrait 1920s cinematic",
    "lucky luciano":  "Lucky Luciano New York mafia portrait cinematic",
    "luciano":        "Lucky Luciano New York mafia portrait cinematic",
    "frank lucas":    "Frank Lucas Harlem drug lord portrait cinematic",
    "griselda blanco":"Griselda Blanco cocaine godmother portrait cinematic",
    "btk":            "BTK killer Dennis Rader portrait dark cinematic",
    "dennis rader":   "BTK killer Dennis Rader portrait dark cinematic",
    "dahmer":         "Jeffrey Dahmer serial killer portrait cinematic",
    "jeffrey dahmer": "Jeffrey Dahmer serial killer portrait cinematic",
    "ed gein":        "Ed Gein Wisconsin criminal portrait dark cinematic",
    "ted bundy":      "Ted Bundy serial killer portrait cinematic",
    "jordan belfort": "Jordan Belfort Wall Street trader portrait cinematic",
    "lindbergh":      "Charles Lindbergh baby kidnapping 1932 historical portrait",
    "el chapo":       "El Chapo Sinaloa cartel leader portrait cinematic",
    "guzman":         "El Chapo Sinaloa cartel leader portrait cinematic",
    "charles manson": "Charles Manson cult leader portrait cinematic",
    "manson":         "Charles Manson cult leader portrait cinematic",
    "john gotti":     "John Gotti New York mafia boss portrait cinematic",
    "gotti":          "John Gotti New York mafia boss portrait cinematic",
    "leopold":        "Leopold and Loeb 1924 Chicago murder case portrait cinematic",
    "loeb":           "Leopold and Loeb 1924 Chicago murder case portrait cinematic",
    "ada":            "1999 Ada Oklahoma murder case portrait dark cinematic",
    # Movies/Series actors
    "scarface":       "Al Pacino Tony Montana Scarface portrait cinematic",
    "tony montana":   "Al Pacino Tony Montana Scarface portrait cinematic",
    "godfather":      "Marlon Brando Don Corleone Godfather portrait cinematic",
    "corleone":       "Marlon Brando Don Corleone Godfather portrait cinematic",
    "breaking bad":   "Bryan Cranston Walter White Breaking Bad portrait",
    "walter white":   "Bryan Cranston Walter White Breaking Bad portrait",
    "dexter":         "Michael C Hall Dexter Morgan portrait dark cinematic",
    "peaky blinders": "Cillian Murphy Tommy Shelby portrait cinematic",
    "tommy shelby":   "Cillian Murphy Tommy Shelby portrait cinematic",
    "money heist":    "Alvaro Morte Professor Money Heist portrait",
    "professor":      "Alvaro Morte Professor Money Heist portrait",
    "ozark":          "Jason Bateman Laura Linney Ozark portrait cinematic",
    "marty byrde":    "Jason Bateman Marty Byrde Ozark portrait cinematic",
    "goodfellas":     "Ray Liotta Henry Hill Goodfellas portrait cinematic",
    "henry hill":     "Ray Liotta Henry Hill Goodfellas portrait cinematic",
    "casino":         "Robert De Niro Sam Rothstein Casino portrait",
    "wolf of wall street": "Leonardo DiCaprio Jordan Belfort portrait cinematic",
    "american gangster":   "Denzel Washington Frank Lucas portrait cinematic",
    "narcos":         "Wagner Moura Pablo Escobar Narcos portrait cinematic",
    "city of god":    "Alexandre Rodrigues Rocket City of God portrait",
    "sicario":        "Emily Blunt Kate Macer Sicario portrait cinematic",
    "griselda":       "Sofia Vergara Griselda Blanco portrait cinematic",
    "cartel":         "cartel leader Mexico dangerous man portrait cinematic",
    "popo":           "cartel leader Mexico dangerous man portrait cinematic",
    "wentworth":      "Danielle Cormack Bea Smith Wentworth portrait",
    "adolescence":    "Stephen Graham Adolescence Netflix portrait cinematic",
    "nypd":           "NYPD detective 1990s New York police portrait cinematic",
}

_LOCATIONS = {
    "colombia":    "Medellin Colombia 1980s barrio street cinematic",
    "brazil":      "Rio de Janeiro Brazil favela cinematic",
    "miami":       "Miami 1980s neon night skyline cinematic",
    "new york":    "New York City 1970s dark street cinematic",
    "chicago":     "Chicago 1920s prohibition era street cinematic",
    "mexico":      "Mexico cartel desert border town cinematic",
    "italy":       "Sicily Italy mafia village cinematic",
    "baltimore":   "Baltimore city street night urban cinematic",
    "oklahoma":    "Oklahoma 1990s rural town cinematic",
    "wisconsin":   "Wisconsin rural dark forest cinematic",
    "harlem":      "Harlem New York 1970s street cinematic",
    "wall street": "Wall Street New York financial district cinematic",
}

_ERAS = {
    "1920": "1920s prohibition era sepia cinematic",
    "1930": "1930s depression era dark cinematic",
    "1950": "1950s vintage americana cinematic",
    "1960": "1960s vintage documentary cinematic",
    "1970": "1970s gritty film grain cinematic",
    "1980": "1980s neon dark cinematic",
    "1990": "1990s gritty urban crime cinematic",
    "2000": "2000s modern crime thriller cinematic",
}

_THEMES = {
    "drug":    "cocaine drug operation laboratory bales cinematic",
    "cartel":  "cartel operation weapons money cinematic",
    "murder":  "crime scene detective investigation dark cinematic",
    "serial":  "psychological thriller dark room evidence cinematic",
    "mafia":   "mafia meeting dark restaurant suits cinematic",
    "heist":   "bank vault robbery masked figures cinematic",
    "fraud":   "financial documents money greed cinematic",
    "kidnap":  "dark room captive dramatic cinematic",
}


def generate_image_prompts(title: str, niche: str, script: str = "", language: str = "english") -> tuple[list[str], int]:
    """Return (list_of_6_prompts, seed) for Pollinations."""
    t = (title + " " + niche).lower()
    s = script.lower()[:500]
    suffix = "vertical 9:16 cinematic portrait dramatic lighting dark background professional 4k photography style"

    # Portrait
    portrait = next(
        (v for k, v in _SUBJECTS.items() if k in t or k in s),
        "true crime documentary mysterious person dark portrait cinematic",
    )

    # Location
    location = next(
        (v for k, v in _LOCATIONS.items() if k in t or k in s),
        "dark city night street dramatic cinematic",
    )

    # Era
    era = next(
        (v for k, v in _ERAS.items() if k in s),
        "modern dark cinematic atmospheric",
    )

    # Theme
    theme = next(
        (v for k, v in _THEMES.items() if k in t or k in s),
        "crime investigation evidence board detective cinematic",
    )

    seed = random.randint(1, 99999)
    prompts = [
        f"{portrait}, {suffix}",
        f"{location}, {suffix}",
        f"{era}, {suffix}",
        f"{theme}, {suffix}",
        f"vintage newspaper headline {portrait}, {suffix}",
        f"courtroom justice verdict dramatic cinematic dark, {suffix}",
    ]
    return prompts, seed


def generate_ai_image(prompt: str, output_path: str, seed: int = None) -> str:
    """Fetch an AI-generated image from Pollinations with retry + dark fallback."""
    import io
    from PIL import Image as PILImage

    output_path = output_path.replace(".jpg", ".png")
    encoded = requests.utils.quote(prompt)
    _seed = seed if seed is not None else random.randint(1, 99999)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1080&height=1920&nologo=true&seed={_seed}"
    )

    for attempt in range(3):
        try:
            response = requests.get(url, timeout=120)
            if response.status_code == 200:
                img = PILImage.open(io.BytesIO(response.content)).convert("RGB")
                img = img.resize((1080, 1920), PILImage.LANCZOS)
                img.save(output_path, "PNG")
                print(f"[Image] Generated: {prompt[:60]}")
                time.sleep(5)
                return output_path
            elif response.status_code == 429:
                print(f"[Image] Rate limited, waiting 30s... (attempt {attempt + 1}/3)")
                time.sleep(30)
            else:
                print(f"[Image] Pollinations returned {response.status_code} (attempt {attempt + 1}/3)")
                time.sleep(10)
        except Exception as e:
            print(f"[Image] Attempt {attempt + 1} failed: {e}")
            time.sleep(15)

    # Fallback: solid dark background so assembly never crashes
    img = PILImage.new("RGB", (1080, 1920), color=(13, 13, 26))
    img.save(output_path, "PNG")
    print(f"[Image] Using dark background fallback for: {prompt[:60]}")
    return output_path


# ── MoviePy clip helpers ───────────────────────────────────────────────────────

def image_to_clips_varied(image_path: str, n_variations: int = 4) -> list:
    """Return up to n_variations clips from one image with zoom and pan effects.
    Uses PIL+numpy to bypass imageio backend on Linux/CI.
    Effects: zoom-in, zoom-out, pan-left, pan-right."""
    import numpy as np
    from PIL import Image as PILImage
    from moviepy import ImageClip
    from moviepy.video.fx import FadeIn, FadeOut

    pil_img = PILImage.open(image_path).convert("RGB")
    img_array = np.array(pil_img)
    h, w = img_array.shape[:2]
    max_pan = max(80, int(w * 0.08))

    def _zoom_clip(scale_fn):
        dur = random.uniform(6, 9)
        c = ImageClip(img_array, duration=dur).resized(scale_fn)
        return c.with_effects([FadeIn(0.5), FadeOut(0.5)])

    def _pan_clip(direction: str):
        """Pan left or right via crop. Falls back to zoom on any error."""
        dur = random.uniform(6, 9)
        try:
            c = ImageClip(img_array, duration=dur)
            if direction == "left":
                # crop window slides rightward → image appears to pan left
                c = c.cropped(
                    x1=lambda t, s=max_pan / dur: int(min(t * s, max_pan)),
                    x2=lambda t, s=max_pan / dur: int(min(t * s + (w - max_pan), w)),
                )
            else:
                # crop window slides leftward → image appears to pan right
                c = c.cropped(
                    x1=lambda t, s=max_pan / dur: int(max(max_pan - t * s, 0)),
                    x2=lambda t, s=max_pan / dur: int(max(max_pan - t * s + (w - max_pan), w - max_pan)),
                )
            c = c.resized((w, h))
            return c.with_effects([FadeIn(0.5), FadeOut(0.5)])
        except Exception:
            return _zoom_clip(lambda t: 1.04 + 0.015 * t)

    all_effects = [
        lambda: _zoom_clip(lambda t: 1.00 + 0.020 * t),  # slow zoom in
        lambda: _zoom_clip(lambda t: 1.12 - 0.020 * t),  # zoom out
        lambda: _pan_clip("left"),                         # pan left
        lambda: _pan_clip("right"),                        # pan right
    ]

    return [fn() for fn in all_effects[:n_variations]]


def _detect_font() -> str | None:
    """Return the first usable font path/name for TextClip on this system."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
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

    # Decide image count and variations based on video type
    is_short     = "short" in video_id
    n_images     = 6  if is_short else 10
    n_variations = 2  if is_short else 4
    print(f"[Video] Generating {n_images} images × {n_variations} variations ({'short' if is_short else 'long'})")

    prompts, seed = generate_image_prompts(title, niche, script_data.get("script", ""), language)
    # Extend prompts to n_images by cycling if needed
    extended_prompts = [prompts[i % len(prompts)] for i in range(n_images)]

    all_clips: list = []
    for i, prompt in enumerate(extended_prompts):
        img_path = os.path.join(IMAGES_DIR, f"{video_id}_img_{i}.png")
        result = generate_ai_image(prompt, img_path, seed=seed + i)
        if result:
            variations = image_to_clips_varied(result, n_variations=n_variations)
            for clip in variations:
                clip = add_text_overlay(clip, "Dark Crime Decoded", "top")
                all_clips.append(clip)
            print(f"[Video] Image {i + 1}/{n_images}: {len(variations)} variations added")
        if i < len(extended_prompts) - 1:
            time.sleep(5)  # respect Pollinations rate limit

    if not all_clips:
        print("[Video] No images generated, skipping")
        return ""

    random.shuffle(all_clips)
    print(f"[Video] Total clip pool: {len(all_clips)} clips (shuffled)")

    video_path = assemble_video(
        audio_path=audio_path,
        image_clips=all_clips,
        output_filename=video_id,
    )
    if video_path:
        script_data["short_clip_path"] = cut_short_clip(video_path, video_id)
    return video_path
